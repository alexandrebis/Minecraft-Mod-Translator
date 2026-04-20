"""
Minecraft mod translation functionality.
"""

import os
import json
import re
import shutil
import argparse
import time
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore
import queue
from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn

# Import retry logic utilities
from ..utils.retry_logic import (
    create_retry_decorator, 
    global_rate_limiter,
    TranslationRateLimiter
)

# Constants
JAR = ".jar"
JSON = ".json"
LANG = ".lang"
MCFUNCTION = ".mcfunction"
DISABLE_LOGS = False

# Global progress tracking
progress_lock = Lock()
global_progress = None


class ThreadSafeProgress:
    """Thread-safe progress tracker for multithreaded operations."""
    
    def __init__(self):
        self.lock = Lock()
        self.progress = None
        self.tasks = {}
    
    def start(self, description="Processing"):
        """Start progress tracking."""
        self.progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.1f}%"),
            TextColumn("•"),
            DownloadColumn(),
            TextColumn("•"),
            TransferSpeedColumn(),
        )
        self.progress.start()
        return self
    
    def add_task(self, description: str, total: int):
        """Add a task to track."""
        with self.lock:
            task_id = self.progress.add_task(description, total=total)
            self.tasks[description] = task_id
            return task_id
    
    def update(self, task_id: int, advance: float = 1):
        """Update a task's progress (thread-safe)."""
        with self.lock:
            if self.progress and task_id is not None:
                self.progress.update(task_id, advance=advance)
    
    def stop(self):
        """Stop progress tracking."""
        if self.progress:
            self.progress.stop()
            self.progress = None

# Logging functions
title = ""


def log_title(new_title: str) -> None:
    """
    Change console title message.
    """
    global title
    if DISABLE_LOGS:
        return
    title = new_title
    clear_console()
    print(f"\n{title}\n")


def log_subtitle(new_message: str) -> None:
    """
    Change console subtitle message.
    """
    global title
    if DISABLE_LOGS:
        return
    title = f"{title}\n{new_message}"
    clear_console()
    print(f"\n{title}\n")


def log_message(message: str) -> None:
    """
    Change console message.
    """
    global title
    if DISABLE_LOGS:
        return
    clear_console()
    print(f"\n{title}\n{message}")


def clear_console() -> None:
    """
    Clear all console messages.
    """
    if DISABLE_LOGS:
        return
    command = ""
    if os.name == "nt":
        command = "cls"
    else:
        command = "clear"
    os.system(command)


# Utility function to handle JSON with comments
def remove_comments_from_json(json_str: str) -> str:
    """
    Remove comments from JSON string. Handles both // and /* */ comment styles.
    """
    # First, remove // style comments (up to end of line)
    result = re.sub(r"//.*$", "", json_str, flags=re.MULTILINE)

    # Then, remove /* */ style comments (can span multiple lines)
    result = re.sub(r"/\*.*?\*/", "", result, flags=re.DOTALL)

    return result


def parse_json_with_comments(file_path: str) -> Dict[str, Any]:
    """
    Parse a JSON file that may contain comments.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Remove comments
        clean_content = remove_comments_from_json(content)

        # Parse the cleaned JSON
        return json.loads(clean_content)
    except Exception as e:
        print(f"Error parsing JSON file {file_path}: {str(e)}")
        return {}


# Translator class
class Translator:
    """
    A class for translating text data from a source language to a target language.
    Supports both Google Translate and OpenAI translation.
    """

    def __init__(
        self, source_language: str, target_language: str, capitalize: bool = True, use_openai: bool = False
    ):
        self.source_language = source_language
        self.target_language = target_language
        self.capitalize = capitalize
        self.use_openai = use_openai
        
        # Initialize retry decorators for both services
        self.google_retry = create_retry_decorator('google', max_retries=3)
        self.openai_retry = create_retry_decorator('openai', max_retries=3)
        
        if self.use_openai:
            self._setup_openai()

    def _setup_openai(self):
        """Setup OpenAI client for AI translation"""
        try:
            from openai import OpenAI
            
            # Load environment variables from .env file
            try:
                from dotenv import load_dotenv
                # Try to load from current working directory and project root
                load_dotenv()  # Load from current directory
                load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))  # Load from project root
                log_message("📄 Loaded .env configuration")
            except ImportError:
                log_message("⚠️ python-dotenv not found. Using system environment variables.")
            
            self.api_key = os.getenv("OPENAI_API_KEY")
            if not self.api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable not set.\n"
                    "Please:\n"
                    "1. Set OPENAI_API_KEY environment variable, or\n"
                    "2. Create a .env file with: OPENAI_API_KEY=your_key_here"
                )
            
            self.openai_client = OpenAI(api_key=self.api_key)
            self.model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
            log_message(f"🤖 OpenAI initialized with model: {self.model}")
            
        except ImportError:
            raise ImportError("OpenAI package not found. Install with: pip install openai python-dotenv")

    def _translate_with_openai(self, text: str) -> str:
        """Translate text using OpenAI with retry logic and rate limiting"""
        if not text.strip():
            return text
        
        # Apply the retry decorator to the actual translation call
        @self.openai_retry
        def _do_openai_translation(text: str) -> str:
            # Apply preventive delay to avoid rate limits
            global_rate_limiter.apply_service_delay('openai')
            
            system_prompt = f"""You are a professional translator specializing in video game localization.
            Translate from {self.source_language} to {self.target_language}.
            
            Guidelines:
            - Preserve formatting like %s, %d, {{}} placeholders
            - Maintain gaming-appropriate tone
            - Use natural, idiomatic expressions
            - Keep technical terms consistent
            
            Respond with ONLY the translated text, no explanations."""
            
            completion = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Translate: {text}"}
                ],
                temperature=0.3,
                max_tokens=500
            )
            
            translated_text = completion.choices[0].message.content.strip()
            
            if self.capitalize and translated_text:
                translated_text = translated_text.capitalize()
                
            return translated_text
        
        try:
            return _do_openai_translation(text)
        except Exception as e:
            log_message(f"OpenAI translation failed after all retries for '{text}': {e}")
            return text  # Return original on error

    def translate_data(self, data: Dict[str, str]) -> Dict[str, str]:
        """
        Translate data from source to target language.
        """
        translation_service = "OpenAI" if self.use_openai else "Google Translate"
        log_message(f"Translating {len(data)} entries from {self.source_language} to {self.target_language} using {translation_service}...")

        if self.use_openai:
            return self._translate_data_openai(data)
        else:
            return self._translate_data_google(data)

    def _translate_data_openai(self, data: Dict[str, str]) -> Dict[str, str]:
        """Translate data using OpenAI with rate limiting protection, threading, and progress tracking"""
        
        translated_data = {}
        total_items = len(data)
        
        # Use threading for OpenAI but with lower concurrency to respect API limits
        # OpenAI has stricter rate limits than Google
        max_workers = min(2, max(1, total_items // 20))  # Very conservative threading
        
        # Create progress tracker
        progress = ThreadSafeProgress()
        progress.start("OpenAI Translation")
        task_id = progress.add_task("Translation", total=total_items)
        
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_key = {}
                
                for index, (key, text) in enumerate(data.items(), 1):
                    if not text or not isinstance(text, str):
                        translated_data[key] = text
                        progress.update(task_id, advance=1)
                        continue
                    
                    # Submit translation task to thread pool
                    future = executor.submit(self._translate_with_openai, text)
                    future_to_key[future] = (key, text, index)
                
                # Process completed translations as they finish
                completed = 0
                for future in as_completed(future_to_key):
                    key, original_text, index = future_to_key[future]
                    try:
                        translated_text = future.result()
                        translated_data[key] = translated_text
                        log_message(f'🤖 [{index}/{total_items}] "{original_text}" → "{translated_text}"')
                    except Exception as e:
                        log_message(f'Error translating "{original_text}": {str(e)}')
                        translated_data[key] = original_text
                    finally:
                        progress.update(task_id, advance=1)
                    
                    completed += 1
                    # Add delay between completions to respect API rate limits
                    if completed < total_items:
                        time.sleep(0.3)
        finally:
            progress.stop()
        
        log_message(f"Successfully translated {len(data)} entries using OpenAI")
        return translated_data

    def _translate_data_google(self, data: Dict[str, str]) -> Dict[str, str]:
        """Translate data using Google Translate with strict rate limiting (5 req/sec), threading, and progress tracking"""
        
        translated_data = {}
        total_items = len(data)
        
        # Google Translate: 5 requests per second max = 200ms per request
        # Use VERY conservative threading to respect rate limits
        # With 200ms per request, even 1 worker is optimal
        max_workers = 1
        
        # Create progress tracker
        progress = ThreadSafeProgress()
        progress.start("Google Translate")
        task_id = progress.add_task("Translation", total=total_items)
        
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_key = {}
                last_request_time = [0]  # Use list to allow modification in nested function
                request_lock = Lock()
                
                def translate_with_rate_limit(text):
                    """Translate with rate limiting: 5 requests per second (200ms minimum between requests)."""
                    with request_lock:
                        # Ensure 200ms minimum between requests
                        elapsed = time.time() - last_request_time[0]
                        min_delay = 0.2  # 5 requests per second
                        if elapsed < min_delay:
                            time.sleep(min_delay - elapsed)
                        last_request_time[0] = time.time()
                    
                    # Now make the actual translation request
                    return self._translate_with_google(text)
                
                for index, (key, text) in enumerate(data.items(), 1):
                    if not text or not isinstance(text, str):
                        # Handle empty/non-string values immediately
                        translated_data[key] = text
                        progress.update(task_id, advance=1)
                        continue
                    
                    # Submit translation task to thread pool
                    future = executor.submit(translate_with_rate_limit, text)
                    future_to_key[future] = (key, text, index)
                
                # Process completed translations as they finish
                for future in as_completed(future_to_key):
                    key, original_text, index = future_to_key[future]
                    try:
                        translated_text = future.result()
                        translated_data[key] = translated_text
                        log_message(f'🌐 [{index}/{total_items}] "{original_text}" → "{translated_text}"')
                    except Exception as e:
                        log_message(f'Error translating "{original_text}": {str(e)}')
                        translated_data[key] = original_text  # Keep original on error
                    finally:
                        progress.update(task_id, advance=1)
        finally:
            progress.stop()
        
        log_message(f"Successfully translated {len(translated_data)} entries using Google Translate (rate limited: 5 req/sec)")
        return translated_data

    def translate(self, string: str) -> str:
        """
        Translate string from source to target language.
        """
        if not string or not isinstance(string, str):
            return string

        if self.use_openai:
            return self._translate_with_openai(string)
        else:
            return self._translate_with_google(string)

    def _translate_with_google(self, string: str) -> str:
        """Translate string using Google Translate with retry logic and rate limiting"""
        
        # Apply the retry decorator to the actual translation call
        @self.google_retry
        def _do_google_translation(text: str) -> str:
            # Apply preventive delay to avoid rate limits
            global_rate_limiter.apply_service_delay('google')
            
            try:
                from deep_translator import GoogleTranslator
            except ImportError:
                raise ImportError("deep_translator package is required for Google translation. Install with: pip install deep-translator")

            translator = GoogleTranslator(
                source=self.source_language, target=self.target_language
            )
            translated_string = translator.translate(text)
            if self.capitalize and translated_string:
                translated_string = translated_string.capitalize()
            return translated_string
        
        try:
            return _do_google_translation(string)
        except ImportError as e:
            print(f"Error: {e}")
            return string
        except Exception as e:
            log_message(f'Google translation failed after all retries for "{string}": {str(e)}')
            return string  # Return original string on error


# Settings class
class Settings:
    """
    The Settings class is responsible for managing configuration settings
    for this Minecraft translation tool.
    """

    def __init__(self, cli_args: argparse.Namespace = None):
        # Default values if no CLI arguments provided
        self.source_mc_lang = self._format_lang("en_US")
        self.target_mc_lang = self._format_lang("es_ES")
        self.mods_path = "./"
        self.temp_path = "temp"
        self.translation_path = "./translated"
        self.use_ai = False  # Default to Google Translate
        self.resume = False  # Default to not resuming

        # Override with CLI arguments if provided
        if cli_args:
            if hasattr(cli_args, "source") and cli_args.source:
                self.source_mc_lang = self._format_lang(cli_args.source)

            if hasattr(cli_args, "target") and cli_args.target:
                self.target_mc_lang = self._format_lang(cli_args.target)

            if hasattr(cli_args, "path") and cli_args.path:
                self.mods_path = cli_args.path

            if hasattr(cli_args, "output") and cli_args.output:
                self.translation_path = cli_args.output
                
            if hasattr(cli_args, "ai") and cli_args.ai:
                self.use_ai = True
                self.source_mc_lang = self._format_lang(cli_args.source)

            if hasattr(cli_args, "target") and cli_args.target:
                self.target_mc_lang = self._format_lang(cli_args.target)

            if hasattr(cli_args, "path") and cli_args.path:
                self.mods_path = cli_args.path

            if hasattr(cli_args, "output") and cli_args.output:
                self.translation_path = cli_args.output

            if hasattr(cli_args, "resume") and cli_args.resume:
                self.resume = True

        # Set Google language codes
        self.source_google_lang = self._get_google_lang(self.source_mc_lang)
        self.target_google_lang = self._get_google_lang(self.target_mc_lang)

    def _get_google_lang(self, mc_lang: str) -> str:
        """
        Get Google language code from Minecraft language code.
        """
        google_lang = mc_lang.split("_")[0]
        return google_lang

    def _replace_appdata(self, path: str) -> str:
        """
        Replace %Appdata% for its respective path.
        """
        pattern = re.compile(r"%appdata%", re.IGNORECASE)
        appdata_path = os.getenv("APPDATA", "").replace("\\", "\\\\")
        new_path = re.sub(pattern, appdata_path, path)
        return new_path

    def _format_lang(self, mc_lang: str) -> str:
        """
        Format language code for Minecraft (e.g., us_US, es_ES...)
        """
        language, region = mc_lang.split("_")
        formatted_language_code = f"{language.lower()}_{region.upper()}"
        return formatted_language_code


# File Manager class
class FileManager:
    """
    A utility class for managing translation of Minecraft mod files.
    """

    def __init__(self, settings: Settings):
        self.temp_path = settings.temp_path
        self.translation_path = settings.translation_path
        self.mods_path = settings.mods_path

        self.source_mc_lang = settings.source_mc_lang
        self.target_mc_lang = settings.target_mc_lang

        # Choose translator based on settings
        if settings.use_ai:
            log_message("🤖 Using OpenAI translator...")
            try:
                self.translator = Translator(
                    settings.source_google_lang, 
                    settings.target_google_lang,
                    use_openai=True
                )
            except (ImportError, ValueError) as e:
                log_message(f"❌ OpenAI initialization failed: {e}")
                log_message("🔄 Falling back to Google Translate...")
                self.translator = Translator(
                    settings.source_google_lang, settings.target_google_lang
                )
        else:
            log_message("🌐 Using Google Translate...")
            self.translator = Translator(
                settings.source_google_lang, settings.target_google_lang
            )

    def create_needed_folders(self) -> None:
        """
        Create necessary folders if they do not exist.
        """
        os.makedirs(self.temp_path, exist_ok=True)
        os.makedirs(self.translation_path, exist_ok=True)

    def check_incomplete_translation(self) -> Optional[Dict[str, Any]]:
        """
        Check if there's an incomplete translation in the temp folder.
        Returns a dictionary with information about the incomplete translation,
        or None if no incomplete translation is found.
        """
        if not os.path.exists(self.temp_path) or not os.listdir(self.temp_path):
            return None

        # Look for target language files in the temp folder
        target_json_lower = f"{self.target_mc_lang.lower()}{JSON}"
        target_json_original = f"{self.target_mc_lang}{JSON}"
        target_lang_lower = f"{self.target_mc_lang.lower()}{LANG}"
        target_lang_original = f"{self.target_mc_lang}{LANG}"

        target_files = []
        source_files = []
        
        source_json_lower = f"{self.source_mc_lang.lower()}{JSON}"
        source_json_original = f"{self.source_mc_lang}{JSON}"
        source_lang_lower = f"{self.source_mc_lang.lower()}{LANG}"
        source_lang_original = f"{self.source_mc_lang}{LANG}"

        for foldername, _, filenames in os.walk(self.temp_path):
            for filename in filenames:
                lower_filename = filename.lower()
                # Check for target language files
                if (
                    lower_filename == target_json_lower.lower()
                    or lower_filename == target_json_original.lower()
                    or lower_filename == target_lang_lower.lower()
                    or lower_filename == target_lang_original.lower()
                ):
                    target_files.append(os.path.join(foldername, filename))
                
                # Check for source language files
                if (
                    lower_filename == source_json_lower.lower()
                    or lower_filename == source_json_original.lower()
                    or lower_filename == source_lang_lower.lower()
                    or lower_filename == source_lang_original.lower()
                ):
                    source_files.append(os.path.join(foldername, filename))

        if target_files:
            return {
                'has_incomplete': True,
                'target_files': target_files,
                'source_files': source_files,
                'target_language': self.target_mc_lang,
                'temp_size': sum(os.path.getsize(f) for f in target_files if os.path.isfile(f))
            }

        return None

    def unpack_mods(self) -> None:
        """
        Unpack all mod.jar files using threading for faster extraction.
        """
        mod_list = os.listdir(self.mods_path)
        jar_files = [m for m in mod_list if m.endswith(JAR)]
        
        if not jar_files:
            log_message("No JAR files found to extract")
            return
        
        # Use threading for faster extraction with progress tracking
        max_workers = min(4, max(1, len(jar_files) // 2))
        
        # Create progress tracker
        progress = ThreadSafeProgress()
        progress.start("Extracting mods")
        task_id = progress.add_task("Extraction", total=len(jar_files))
        
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                
                for mod_name in jar_files:
                    future = executor.submit(self._unpack_single_mod, mod_name)
                    futures[future] = mod_name
                
                # Process results as they complete
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        mod_name = futures[future]
                        log_message(f"Error extracting {mod_name}: {str(e)}")
                    finally:
                        progress.update(task_id, advance=1)
        finally:
            progress.stop()

    def _unpack_single_mod(self, mod_name: str) -> None:
        """
        Unpack a single mod JAR file (used for threading).
        """
        mod_file_path = os.path.join(self.mods_path, mod_name)
        unpacking_destination = os.path.join(self.temp_path, mod_name)
        try:
            with ZipFile(mod_file_path, "r") as zip:
                log_message(f"Unpacking {mod_name}...")
                zip.extractall(unpacking_destination)
                log_message(f"Successfully extracted {mod_name}")
        except Exception as e:
            log_message(f"Error unpacking {mod_name}: {str(e)}")
            raise

    def get_lang_folders(self) -> List[str]:
        """
        Get all language folder paths and mcfunction root folders.
        """
        lang_folders = []
        found_files = []
        log_message(f"Searching for language files in {self.temp_path}...")

        # First, find all language files in the extracted mods
        for foldername, _, filenames in os.walk(self.temp_path):
            source_json_lower = f"{self.source_mc_lang.lower()}{JSON}"
            source_json_original = f"{self.source_mc_lang}{JSON}"
            source_lang_lower = f"{self.source_mc_lang.lower()}{LANG}"
            source_lang_original = f"{self.source_mc_lang}{LANG}"

            for filename in filenames:
                lower_filename = filename.lower()
                if (
                    lower_filename == source_json_lower.lower()
                    or lower_filename == source_json_original.lower()
                    or lower_filename == source_lang_lower.lower()
                    or lower_filename == source_lang_original.lower()
                ):
                    found_files.append((foldername, filename))

        # Now find the language folders (typically containing "lang" in the path)
        for folder, filename in found_files:
            if "lang" in folder.lower():
                if folder not in lang_folders:
                    lang_folders.append(folder)
                    mod_path_parts = folder.split(os.sep)
                    mod_name = (
                        mod_path_parts[1] if len(mod_path_parts) > 1 else "unknown"
                    )
                    log_message(f"Found language folder: {folder}")
                    log_message(f"Contains source file: {filename}")
            else:
                # If we didn't find a folder with "lang" in the name, use the parent directory
                parent_folder = os.path.dirname(folder)
                if parent_folder not in lang_folders:
                    lang_folders.append(parent_folder)
                    mod_path_parts = parent_folder.split(os.sep)
                    mod_name = (
                        mod_path_parts[1] if len(mod_path_parts) > 1 else "unknown"
                    )
                    log_message(
                        f"Found language file outside standard lang folder: {folder}"
                    )
                    log_message(f"Using parent folder: {parent_folder}")

        # Also search for .mcfunction files and add their root mod folders
        mcfunction_folders = self.get_mcfunction_folders()
        for folder in mcfunction_folders:
            if folder not in lang_folders:
                lang_folders.append(folder)

        if not lang_folders:
            log_message(
                f"Warning: No language folders found containing {self.source_mc_lang} files"
            )
            # List all directories to help with debugging
            log_message("Available directories:")
            for dirpath, dirnames, _ in os.walk(self.temp_path):
                if "lang" in dirpath.lower() or "assets" in dirpath.lower():
                    log_message(f"  - {dirpath}")
                    log_message(f"    Contents: {os.listdir(dirpath)}")

        return lang_folders

    def get_mcfunction_folders(self) -> List[str]:
        """
        Get all mod root folders that contain .mcfunction files.
        """
        mcfunction_folders = []
        log_message(f"Searching for .mcfunction files in {self.temp_path}...")

        # Find all .mcfunction files in the extracted mods
        for foldername, _, filenames in os.walk(self.temp_path):
            for filename in filenames:
                if filename.endswith(MCFUNCTION):
                    # Get the mod root folder (first level under temp_path)
                    path_parts = foldername.split(os.sep)
                    temp_parts = self.temp_path.split(os.sep)
                    
                    # Find the mod root - it's the directory immediately under temp_path
                    if len(path_parts) > len(temp_parts):
                        mod_root_parts = temp_parts + [path_parts[len(temp_parts)]]
                        mod_root = os.sep.join(mod_root_parts)
                        
                        if mod_root not in mcfunction_folders:
                            mcfunction_folders.append(mod_root)
                            mod_name = path_parts[len(temp_parts)] if len(path_parts) > len(temp_parts) else "unknown"
                            log_message(f"Found mod with .mcfunction files: {mod_name}")
                            break  # No need to continue checking this folder

        return mcfunction_folders

    def edit_lang_files(self, lang_folders: List[str]) -> None:
        """
        Translate the source language files to the target language using threading.
        """
        if not lang_folders:
            log_message("No language folders to process")
            return
        
        # Use threading to process multiple language folders in parallel
        max_workers = min(4, max(1, len(lang_folders) // 3))
        
        # Create progress tracker
        progress = ThreadSafeProgress()
        progress.start("Processing language folders")
        task_id = progress.add_task("Translation", total=len(lang_folders))
        
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                
                for lang_folder in lang_folders:
                    # Submit each folder processing to thread pool
                    future = executor.submit(self._process_lang_folder, lang_folder)
                    futures[future] = lang_folder
                
                # Process results as they complete
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        lang_folder = futures[future]
                        log_message(f"Error processing {lang_folder}: {str(e)}")
                    finally:
                        progress.update(task_id, advance=1)
        finally:
            progress.stop()

    def _process_lang_folder(self, lang_folder: str) -> None:
        """
        Process a single language folder (used for threading).
        """
        # Extract mod name from the path in a platform-independent way
        path_parts = lang_folder.split(os.sep)
        # The mod name is typically the 2nd element in the path (after temp directory)
        if len(path_parts) > 1:
            mod_name = path_parts[1]
            mod_name = mod_name.replace(JAR, "")
        else:
            mod_name = "unknown-mod"

        log_subtitle(f"Processing {mod_name}...")

        # Check if source language files exist before attempting translation
        source_json_path = os.path.join(
            lang_folder, f"{self.source_mc_lang.lower()}{JSON}"
        )
        source_lang_path = os.path.join(lang_folder, f"{self.source_mc_lang}{LANG}")

        # Target language file paths
        target_json_path = os.path.join(
            lang_folder, f"{self.target_mc_lang.lower()}{JSON}"
        )
        target_lang_path = os.path.join(lang_folder, f"{self.target_mc_lang}{LANG}")

        # Flag to track if we found and processed any files
        files_processed = False

        # Check and process JSON files
        if os.path.exists(source_json_path):
            log_subtitle(
                f"Creating {self.target_mc_lang.lower()}{JSON} from {self.source_mc_lang.lower()}{JSON}..."
            )
            original_data = self._read_json_file(source_json_path)
            if original_data:
                # Check if target file already exists (resume optimization)
                translated_data = {}
                if os.path.exists(target_json_path):
                    existing_data = self._read_json_file(target_json_path)
                    if existing_data:
                        translated_data = existing_data.copy()
                        # Only translate keys that don't exist or are empty
                        keys_to_translate = {k: v for k, v in original_data.items() 
                                           if k not in translated_data or not translated_data[k]}
                        if keys_to_translate:
                            log_message(f"Resuming: {len(keys_to_translate)} new/modified entries to translate")
                            new_translations = self.translator.translate_data(keys_to_translate)
                            translated_data.update(new_translations)
                        else:
                            log_message(f"Skipping: All entries already translated for {mod_name}")
                    else:
                        # Target exists but is empty, translate all
                        translated_data = self.translator.translate_data(original_data)
                else:
                    # Target doesn't exist, translate all
                    translated_data = self.translator.translate_data(original_data)
                
                self._write_json_file(translated_data, target_json_path)
                log_message(f"Successfully translated JSON file for {mod_name}")
                files_processed = True
            else:
                log_message(f"No data found in source JSON file for {mod_name}")

        # Check and process LANG files
        if os.path.exists(source_lang_path):
            log_subtitle(
                f"Creating {self.target_mc_lang}{LANG} from {self.source_mc_lang}{LANG}..."
            )
            original_data = self._read_lang_file(source_lang_path)
            if original_data:
                # Check if target file already exists (resume optimization)
                translated_data = {}
                if os.path.exists(target_lang_path):
                    existing_data = self._read_lang_file(target_lang_path)
                    if existing_data:
                        translated_data = existing_data.copy()
                        # Only translate keys that don't exist or are empty
                        keys_to_translate = {k: v for k, v in original_data.items() 
                                           if k not in translated_data or not translated_data[k]}
                        if keys_to_translate:
                            log_message(f"Resuming: {len(keys_to_translate)} new/modified entries to translate")
                            new_translations = self.translator.translate_data(keys_to_translate)
                            translated_data.update(new_translations)
                        else:
                            log_message(f"Skipping: All entries already translated for {mod_name}")
                    else:
                        # Target exists but is empty, translate all
                        translated_data = self.translator.translate_data(original_data)
                else:
                    # Target doesn't exist, translate all
                    translated_data = self.translator.translate_data(original_data)
                
                self._write_lang_file(translated_data, target_lang_path)
                log_message(f"Successfully translated LANG file for {mod_name}")
                files_processed = True
            else:
                log_message(f"No data found in source LANG file for {mod_name}")

        # If no exact match found, try case-insensitive search
        if not files_processed:
            log_message(
                f"Searching for alternative source files in {lang_folder}..."
            )
            for filename in os.listdir(lang_folder):
                lower_filename = filename.lower()

                # Try to find JSON files with case-insensitive matching
                if lower_filename == f"{self.source_mc_lang.lower()}{JSON}".lower():
                    source_file_path = os.path.join(lang_folder, filename)
                    target_file_path = os.path.join(
                        lang_folder, f"{self.target_mc_lang.lower()}{JSON}"
                    )

                    log_subtitle(
                        f"Creating {self.target_mc_lang.lower()}{JSON} from {filename}..."
                    )
                    original_data = self._read_json_file(source_file_path)
                    if original_data:
                        # Check if target file already exists (resume optimization)
                        translated_data = {}
                        if os.path.exists(target_file_path):
                            existing_data = self._read_json_file(target_file_path)
                            if existing_data:
                                translated_data = existing_data.copy()
                                keys_to_translate = {k: v for k, v in original_data.items() 
                                                   if k not in translated_data or not translated_data[k]}
                                if keys_to_translate:
                                    log_message(f"Resuming: {len(keys_to_translate)} new/modified entries to translate")
                                    new_translations = self.translator.translate_data(keys_to_translate)
                                    translated_data.update(new_translations)
                                else:
                                    log_message(f"Skipping: All entries already translated")
                            else:
                                translated_data = self.translator.translate_data(original_data)
                        else:
                            translated_data = self.translator.translate_data(original_data)
                        
                        self._write_json_file(translated_data, target_file_path)
                        log_message(
                            f"Successfully translated JSON file for {mod_name}"
                        )
                        files_processed = True
                    else:
                        log_message(
                            f"No data found in source JSON file for {mod_name}"
                        )

                # Try to find LANG files with case-insensitive matching
                elif lower_filename == f"{self.source_mc_lang}{LANG}".lower():
                    source_file_path = os.path.join(lang_folder, filename)
                    target_file_path = os.path.join(
                        lang_folder, f"{self.target_mc_lang}{LANG}"
                    )

                    log_subtitle(
                        f"Creating {self.target_mc_lang}{LANG} from {filename}..."
                    )
                    original_data = self._read_lang_file(source_file_path)
                    if original_data:
                        # Check if target file already exists (resume optimization)
                        translated_data = {}
                        if os.path.exists(target_file_path):
                            existing_data = self._read_lang_file(target_file_path)
                            if existing_data:
                                translated_data = existing_data.copy()
                                keys_to_translate = {k: v for k, v in original_data.items() 
                                                   if k not in translated_data or not translated_data[k]}
                                if keys_to_translate:
                                    log_message(f"Resuming: {len(keys_to_translate)} new/modified entries to translate")
                                    new_translations = self.translator.translate_data(keys_to_translate)
                                    translated_data.update(new_translations)
                                else:
                                    log_message(f"Skipping: All entries already translated")
                            else:
                                translated_data = self.translator.translate_data(original_data)
                        else:
                            translated_data = self.translator.translate_data(original_data)
                        
                        self._write_lang_file(translated_data, target_file_path)
                        log_message(
                            f"Successfully translated LANG file for {mod_name}"
                        )
                        files_processed = True
                    else:
                        log_message(
                            f"No data found in source LANG file for {mod_name}"
                        )

        if not files_processed:
            log_message(f"No translatable language files found for {mod_name}")

        # Check if this is a mod root folder and process .mcfunction files
        # A mod root folder is directly under temp_path
        temp_path_parts = self.temp_path.split(os.sep)
        lang_folder_parts = lang_folder.split(os.sep)
        
        # Check if this folder is a mod root (temp_path + one level)
        if len(lang_folder_parts) == len(temp_path_parts) + 1:
            log_subtitle(f"Checking for .mcfunction files in {mod_name}...")
            self.translate_mcfunction_files(lang_folder)
            files_processed = True

    # Removed _translate_mod function as it's no longer needed

    def _read_json_file(self, path: str) -> Dict[str, str]:
        """
        Read JSON file data.
        """
        try:
            # Use our custom JSON parser that can handle comments
            data = parse_json_with_comments(path)

            if data:
                log_message(f"Successfully loaded JSON with {len(data)} entries")
            else:
                log_message(f"Warning: No data found in JSON file: {path}")

            return data
        except Exception as e:
            log_message(f"Error reading JSON file {path}: {str(e)}")
            return {}

    def _read_lang_file(self, path: str) -> Dict[str, str]:
        """
        Read LANG file.
        """
        data = {}
        with open(path, "r") as file:
            lines = file.readlines()
        for line in lines:
            line = line.strip()
            if line:
                try:
                    key, value = line.split("=", 1)  # Split on first equals sign only
                    data[key] = value
                except ValueError:
                    # Skip lines that don't have key=value format
                    pass
        return data

    def _write_json_file(self, data: Dict[str, str], path: str) -> None:
        """
        Write JSON file.
        """
        try:
            log_message(f"Writing JSON file to {path}")
            os.makedirs(os.path.dirname(path), exist_ok=True)

            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4, ensure_ascii=False)

            if os.path.exists(path):
                file_size = os.path.getsize(path)
                log_message(f"JSON file successfully written ({file_size} bytes)")
                # Verify file content
                with open(path, "r", encoding="utf-8") as check_file:
                    content = check_file.read()
                    if len(content) > 0:
                        log_message(f"Verified file content: {len(content)} bytes")
                    else:
                        log_message(f"WARNING: File exists but is empty")
            else:
                log_message(f"WARNING: Failed to create file {path}")
        except Exception as e:
            log_message(f"ERROR writing JSON file: {str(e)}")

    def _write_lang_file(self, data: Dict[str, str], path: str) -> None:
        """
        Write LANG file.
        """
        try:
            log_message(f"Writing LANG file to {path}")
            os.makedirs(os.path.dirname(path), exist_ok=True)

            text = ""
            for key, value in data.items():
                text += f"{key}={value}\n"

            with open(path, "w", encoding="utf-8") as file:
                file.write(text)

            if os.path.exists(path):
                file_size = os.path.getsize(path)
                log_message(f"LANG file successfully written ({file_size} bytes)")
            else:
                log_message(f"WARNING: Failed to create file {path}")
        except Exception as e:
            log_message(f"ERROR writing LANG file: {str(e)}")

    def _read_mcfunction_file(self, path: str) -> Dict[str, str]:
        """
        Read MCFUNCTION file and extract translatable text from data modify storage commands.
        Returns a dictionary where keys are unique identifiers and values are the translatable text.
        """
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as file:
                lines = file.readlines()
            
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                # Look for data modify storage commands with quoted text
                if "data modify storage" in line and "set value" in line:
                    # Extract text between double quotes, handling escaped quotes
                    # This regex matches the content between quotes, handling escaped quotes
                    match = re.search(r'set value "([^"\\]*(?:\\.[^"\\]*)*)"', line)
                    if match:
                        text = match.group(1)
                        # Unescape the text for translation
                        text = text.replace('\\"', '"')
                        # Use file path and line number as unique key
                        key = f"{path}:{line_num}"
                        data[key] = text
                        
            log_message(f"Extracted {len(data)} translatable strings from {path}")
            return data
        except Exception as e:
            log_message(f"Error reading MCFUNCTION file {path}: {str(e)}")
            return {}

    def _write_mcfunction_file(self, original_path: str, translated_data: Dict[str, str]) -> None:
        """
        Write MCFUNCTION file with translated text, preserving the original structure.
        """
        try:
            log_message(f"Writing MCFUNCTION file to {original_path}")
            
            with open(original_path, "r", encoding="utf-8") as file:
                lines = file.readlines()
            
            # Process each line and replace translatable text
            for line_num, line in enumerate(lines):
                original_line = line.strip()
                if "data modify storage" in original_line and "set value" in original_line:
                    # Find the corresponding translated text
                    key = f"{original_path}:{line_num + 1}"
                    if key in translated_data:
                        # Replace the quoted text with translated version
                        translated_text = translated_data[key]
                        # Escape any quotes in the translated text
                        escaped_text = translated_text.replace('"', '\\"')
                        # Replace the original quoted text with translated text
                        # This regex handles escaped quotes properly
                        new_line = re.sub(r'(set value )"([^"\\]*(?:\\.[^"\\]*)*)"', f'\\1"{escaped_text}"', line)
                        lines[line_num] = new_line
            
            # Write the modified content back to the file
            with open(original_path, "w", encoding="utf-8") as file:
                file.writelines(lines)
                
            file_size = os.path.getsize(original_path)
            log_message(f"MCFUNCTION file successfully updated ({file_size} bytes)")
        except Exception as e:
            log_message(f"ERROR writing MCFUNCTION file: {str(e)}")

    def translate_mcfunction_files(self, mod_root_path: str) -> None:
        """
        Find and translate all .mcfunction files in a mod using threading with progress tracking.
        """
        mcfunction_files = []
        
        # Find all .mcfunction files in the mod
        for foldername, _, filenames in os.walk(mod_root_path):
            for filename in filenames:
                if filename.endswith(MCFUNCTION):
                    file_path = os.path.join(foldername, filename)
                    mcfunction_files.append(file_path)
        
        if not mcfunction_files:
            return
            
        log_message(f"Found {len(mcfunction_files)} .mcfunction files to translate")
        
        # Use threading for mcfunction file processing
        max_workers = min(3, max(1, len(mcfunction_files) // 2))
        
        # Create progress tracker
        progress = ThreadSafeProgress()
        progress.start("Processing MCFunction files")
        task_id = progress.add_task("MCFunction", total=len(mcfunction_files))
        
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                
                for file_path in mcfunction_files:
                    future = executor.submit(self._translate_single_mcfunction, file_path)
                    futures[future] = file_path
                
                # Process results as they complete
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        file_path = futures[future]
                        log_message(f"Error translating {file_path}: {str(e)}")
                    finally:
                        progress.update(task_id, advance=1)
        finally:
            progress.stop()

    def _translate_single_mcfunction(self, file_path: str) -> None:
        """
        Translate a single mcfunction file (used for threading).
        """
        log_message(f"Processing {file_path}")
        
        # Read translatable text from the file
        original_data = self._read_mcfunction_file(file_path)
        
        if original_data:
            # Translate the extracted text
            translated_data = self.translator.translate_data(original_data)
            
            # Write back the translated content
            self._write_mcfunction_file(file_path, translated_data)
            
            log_message(f"Successfully translated {len(original_data)} strings in {file_path}")
        else:
            log_message(f"No translatable content found in {file_path}")

    def convert_translated_mods(self) -> None:
        """
        Convert all translated mod folders into JAR files using threading with progress tracking.
        """
        mod_folder_list = os.listdir(self.temp_path)
        
        if not mod_folder_list:
            log_message("No mod folders found to convert")
            return
        
        # Use threading for JAR file creation
        max_workers = min(3, max(1, len(mod_folder_list) // 2))
        
        # Create progress tracker
        progress = ThreadSafeProgress()
        progress.start("Creating JAR files")
        task_id = progress.add_task("JAR Creation", total=len(mod_folder_list))
        
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                
                for mod_folder in mod_folder_list:
                    future = executor.submit(self._convert_single_mod, mod_folder)
                    futures[future] = mod_folder
                
                # Process results as they complete
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        mod_folder = futures[future]
                        log_message(f"Error converting {mod_folder}: {str(e)}")
                    finally:
                        progress.update(task_id, advance=1)
        finally:
            progress.stop()

    def _convert_single_mod(self, mod_folder: str) -> None:
        """
        Convert a single mod folder to JAR (used for threading).
        """
        log_message(f'Converting {mod_folder} into mod file...')
        unpacked_mod_path = os.path.join(self.temp_path, mod_folder)

        # Check if input and output paths are the same
        same_paths = os.path.abspath(self.mods_path) == os.path.abspath(self.translation_path)

        # If same paths, create JAR directly in mods_path to avoid file access issues
        if same_paths:
            translation_path = os.path.join(self.mods_path, mod_folder)
        else:
            translation_path = os.path.join(self.translation_path, mod_folder)

        self._convert_folder_to_jar(unpacked_mod_path, translation_path)

    def _convert_folder_to_jar(self, folder_path: str, jar_path: str) -> None:
        """
        Convert folder to JAR file.
        """
        try:
            log_message(f"Creating JAR file: {jar_path}")
            # Ensure the target directory exists
            os.makedirs(os.path.dirname(jar_path), exist_ok=True)

            # First, check if the target language files exist
            lang_files_found = []
            for root, _, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith(".json") or file.lower().endswith(".lang"):
                        if self.target_mc_lang.lower() in file.lower():
                            relative_path = os.path.relpath(
                                os.path.join(root, file), folder_path
                            )
                            lang_files_found.append(relative_path)
                            log_message(f"Found target language file: {relative_path}")

            if not lang_files_found:
                log_message(
                    "WARNING: No target language files found in the folder to be packaged! Looking for source files..."
                )

                # If no target files are found, try to generate them from source files
                source_files_found = []
                for root, _, files in os.walk(folder_path):
                    for file in files:
                        if file.lower().endswith(".json") or file.lower().endswith(
                            ".lang"
                        ):
                            if self.source_mc_lang.lower() in file.lower():
                                source_path = os.path.join(root, file)
                                target_path = source_path.replace(
                                    self.source_mc_lang.lower(),
                                    self.target_mc_lang.lower(),
                                )
                                source_files_found.append(
                                    (source_path, target_path, file)
                                )
                                log_message(f"Found source language file: {file}")

                # Try to translate the source files
                for source_path, target_path, filename in source_files_found:
                    extension = os.path.splitext(filename)[1].lower()
                    if extension == JSON:
                        log_message(f"Translating source file: {filename}")
                        original_data = self._read_json_file(source_path)
                        if original_data:
                            translated_data = self.translator.translate_data(
                                original_data
                            )
                            self._write_json_file(translated_data, target_path)
                            relative_path = os.path.relpath(target_path, folder_path)
                            lang_files_found.append(relative_path)
                            log_message(
                                f"Created target language file: {os.path.basename(target_path)}"
                            )
                    elif extension == LANG:
                        log_message(f"Translating source file: {filename}")
                        original_data = self._read_lang_file(source_path)
                        if original_data:
                            translated_data = self.translator.translate_data(
                                original_data
                            )
                            self._write_lang_file(translated_data, target_path)
                            relative_path = os.path.relpath(target_path, folder_path)
                            lang_files_found.append(relative_path)
                            log_message(
                                f"Created target language file: {os.path.basename(target_path)}"
                            )

            # Create the JAR file
            file_count = 0
            with ZipFile(jar_path, "w", ZIP_DEFLATED) as jar:
                for root, _, files in os.walk(folder_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, folder_path)
                        jar.write(file_path, relative_path)
                        file_count += 1

                        # Log when we find a target language file
                        if file.lower().endswith(".json") or file.lower().endswith(
                            ".lang"
                        ):
                            if self.target_mc_lang.lower() in file.lower():
                                log_message(
                                    f"Added target language file to JAR: {relative_path}"
                                )

            # Verify the JAR was created
            if os.path.exists(jar_path):
                jar_size = os.path.getsize(jar_path)
                log_message(
                    f"Successfully created JAR file with {file_count} files ({jar_size} bytes)"
                )
            else:
                log_message(f"ERROR: Failed to create JAR file {jar_path}")
        except Exception as e:
            log_message(f"ERROR creating JAR file: {str(e)}")

    def process_single_jar(self, jar_file: str) -> bool:
        """
        Process a single JAR file: extract, translate, and generate output.
        Returns True if successful, False if skipped or failed.
        """
        jar_path = os.path.join(self.mods_path, jar_file)
        jar_name = jar_file.replace(JAR, "")
        
        # Check if output already exists (resume optimization)
        same_paths = os.path.abspath(self.mods_path) == os.path.abspath(self.translation_path)
        if same_paths:
            output_jar_path = os.path.join(self.mods_path, jar_file)
        else:
            output_jar_path = os.path.join(self.translation_path, jar_file)
        
        # Skip if already translated
        if os.path.exists(output_jar_path):
            return (True, jar_file, "already_translated")
        
        temp_mod_path = os.path.join(self.temp_path, jar_file)
        
        try:
            # Extract this JAR only
            # (Logs disabled during progress bar)
            with ZipFile(jar_path, "r") as zip:
                zip.extractall(temp_mod_path)
            
            # Find language folders in this JAR
            lang_folders = self._get_lang_folders_for_mod(temp_mod_path, jar_file)
            
            if lang_folders:
                # Translate language files
                # (Logs disabled during progress bar)
                self._process_lang_folder(temp_mod_path)
                
                # Generate output JAR
                self._convert_folder_to_jar(temp_mod_path, output_jar_path)
                
                return (True, jar_file, "processed")
            else:
                return (True, jar_file, "no_language_files")
                
        except Exception as e:
            # (Error logging will be handled after progress bar stops)
            return (False, jar_file, "error")
        finally:
            # Clean up temp folder for this JAR
            if os.path.exists(temp_mod_path):
                try:
                    shutil.rmtree(temp_mod_path)
                except:
                    pass

    def _get_lang_folders_for_mod(self, mod_path: str, jar_name: str) -> List[str]:
        """
        Get language folders for a specific mod.
        """
        lang_folders = []
        found_files = []
        
        # Find language files in this mod
        for foldername, _, filenames in os.walk(mod_path):
            for filename in filenames:
                lower_filename = filename.lower()
                if (lower_filename == f"{self.source_mc_lang.lower()}{JSON}".lower() or
                    lower_filename == f"{self.source_mc_lang}{JSON}".lower() or
                    lower_filename == f"{self.source_mc_lang.lower()}{LANG}".lower() or
                    lower_filename == f"{self.source_mc_lang}{LANG}".lower()):
                    found_files.append((foldername, filename))
        
        # Get language folders
        for folder, filename in found_files:
            if "lang" in folder.lower():
                if folder not in lang_folders:
                    lang_folders.append(folder)
            else:
                parent_folder = os.path.dirname(folder)
                if parent_folder not in lang_folders:
                    lang_folders.append(parent_folder)
        
        # Add mcfunction root if exists
        mcfunction_exists = False
        for foldername, _, filenames in os.walk(mod_path):
            for filename in filenames:
                if filename.endswith(MCFUNCTION):
                    mcfunction_exists = True
                    break
            if mcfunction_exists:
                break
        
        if mcfunction_exists and mod_path not in lang_folders:
            lang_folders.append(mod_path)
        
        return lang_folders

    def remove_original_mod_files(self) -> None:
        """
        Remove original JAR files from mods folder, but leave folders intact.
        When input and output paths are the same, we want to overwrite specific
        files but not delete the entire directory structure.
        """
        # Before removing files, check if translated files exist
        translated_jars = [
            f for f in os.listdir(self.translation_path) if f.endswith(JAR)
        ]

        if not translated_jars:
            log_message(
                "WARNING: No translated JAR files found. Skipping removal of original files."
            )
            return

        log_message(
            f"Found {len(translated_jars)} translated JAR files. Proceeding with removal of originals."
        )

        # Count how many files we'll actually remove
        files_to_remove = []
        for filename in os.listdir(self.mods_path):
            file_path = os.path.join(self.mods_path, filename)
            # Only remove JAR files, leave directories intact
            if os.path.isfile(file_path) and file_path.endswith(JAR):
                # Only add to removal list if we have a corresponding translated file
                if filename in translated_jars:
                    files_to_remove.append(file_path)

        log_message(
            f"Will remove {len(files_to_remove)} original JAR files that have translated versions"
        )

        # Now actually remove the files
        for file_path in files_to_remove:
            try:
                os.remove(file_path)
                log_message(f"Removed original JAR file: {file_path}")
            except Exception as e:
                log_message(f"Error removing original JAR file {file_path}: {e}")

        # Ensure we don't accidentally empty directories
        log_message(f"Original JAR files removed. Directories preserved.")

    def move_translated_mod_files(self) -> None:
        """
        Move files to output folder.
        """
        for filename in os.listdir(self.translation_path):
            source_path = os.path.join(self.translation_path, filename)
            destination_path = os.path.join(self.mods_path, filename)

            # Check if source and destination are the same
            if os.path.abspath(source_path) == os.path.abspath(destination_path):
                log_message(
                    f"Skipping {filename} as source and destination are the same"
                )
                continue

            log_message(
                f"Moving {filename} from {self.translation_path} to {self.mods_path}"
            )

            # Handle file or directory differently
            if os.path.isfile(source_path):
                shutil.copy2(source_path, destination_path)
                log_message(f"Copied file {filename}")
            elif os.path.isdir(source_path):
                # For directories, merge contents rather than replacing
                if os.path.exists(destination_path):
                    # Merge directory contents without deleting existing files
                    for root, dirs, files in os.walk(source_path):
                        # Get relative path from source
                        rel_path = os.path.relpath(root, source_path)
                        if rel_path == ".":
                            rel_path = ""

                        # Create target directory
                        target_dir = os.path.join(destination_path, rel_path)
                        os.makedirs(target_dir, exist_ok=True)

                        # Copy all files
                        for file in files:
                            src_file = os.path.join(root, file)
                            dst_file = os.path.join(target_dir, file)
                            shutil.copy2(src_file, dst_file)
                            log_message(f"Copied {os.path.join(rel_path, file)}")
                else:
                    # If destination doesn't exist, copy the whole directory
                    shutil.copytree(source_path, destination_path)
                    log_message(f"Copied directory {filename}")

            log_message(f"Successfully moved {filename}")

    def copy_translated_files_to_same_path(self) -> None:
        """
        When input and output paths are the same, we need to copy translated files
        from the temp directory back to the input path, overwriting the original files
        but preserving the directory structure.
        """
        log_message(f"Copying translated files to original directory: {self.mods_path}")

        # Make sure the mods_path exists for the copy operation
        os.makedirs(self.mods_path, exist_ok=True)

        jar_files_copied = 0

        # First, find all translated JAR files in translation_path
        for filename in os.listdir(self.translation_path):
            source_path = os.path.join(self.translation_path, filename)
            dest_path = os.path.join(self.mods_path, filename)

            if os.path.isfile(source_path) and source_path.endswith(JAR):
                # Copy JAR files, overwriting any existing ones
                log_message(f"Copying JAR file: {filename}")
                try:
                    shutil.copy2(source_path, dest_path)
                    jar_files_copied += 1
                    log_message(f"Copied {filename} to {self.mods_path}")
                except Exception as e:
                    log_message(f"Error copying JAR file {filename}: {e}")
            elif os.path.isdir(source_path):
                # For directories, we need to be careful not to delete anything
                # that wasn't part of the translation
                log_message(f"Processing directory: {filename}")

                # For each unpacked mod folder, copy content carefully
                for root, dirs, files in os.walk(source_path):
                    # Get the relative path from the translation path
                    rel_path = os.path.relpath(root, source_path)
                    if rel_path == ".":
                        rel_path = ""

                    # Create the corresponding directory in the original path
                    target_dir = os.path.join(dest_path, rel_path)
                    os.makedirs(target_dir, exist_ok=True)

                    # Copy all files in the directory
                    for file in files:
                        src_file = os.path.join(root, file)
                        dst_file = os.path.join(target_dir, file)
                        try:
                            shutil.copy2(src_file, dst_file)
                            log_message(f"Copied file: {os.path.join(rel_path, file)}")
                        except Exception as e:
                            log_message(
                                f"Error copying file {os.path.join(rel_path, file)}: {e}"
                            )

        if jar_files_copied > 0:
            log_message(
                f"Successfully copied {jar_files_copied} JAR files to {self.mods_path}"
            )
        else:
            log_message(f"WARNING: No JAR files were copied to {self.mods_path}")

        # Verify JAR files in the destination directory
        jar_files_in_dest = [f for f in os.listdir(self.mods_path) if f.endswith(JAR)]
        log_message(
            f"JAR files in destination directory after copying: {len(jar_files_in_dest)}"
        )
        for jar_file in jar_files_in_dest:
            jar_path = os.path.join(self.mods_path, jar_file)
            jar_size = os.path.getsize(jar_path)
            log_message(f"  - {jar_file} ({jar_size} bytes)")

        log_message(f"Successfully copied all translated files to {self.mods_path}")

    def remove_folder(self, folder_path: str) -> None:
        """
        Remove entire folder.
        """
        shutil.rmtree(folder_path)


def add_translate_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add arguments for the translate command.

    Args:
        parser: ArgumentParser object
    """
    parser.add_argument(
        "-p", "--path", help="Path to mod or mod folder", default="./mods"
    )
    parser.add_argument("-s", "--source", help="Source language code (e.g., en_US)")
    parser.add_argument("-t", "--target", help="Target language code (e.g., es_ES)")
    parser.add_argument("-o", "--output", help="Output folder path")
    parser.add_argument(
        "--ai", action="store_true", help="Use OpenAI translation instead of Google Translate"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Resume a previously started translation"
    )


def handle_translate_command(args: argparse.Namespace) -> None:
    """
    Handle the translate command.
    
    Args:
        args: ArgumentParser arguments
    """
    try:
        # Check if AI translation is requested
        if hasattr(args, "ai") and args.ai:
            log_message("🤖 AI translation mode enabled")
            # Check if OpenAI dependencies are available
            try:
                import openai
                # Try to load .env file first
                try:
                    from dotenv import load_dotenv
                    load_dotenv()  # This loads the .env file from current directory
                    log_message("📄 Loaded .env file")
                except ImportError:
                    log_message("⚠️ python-dotenv not found, using system environment variables")
                
                # Now check for API key after loading .env
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    print("❌ Error: OPENAI_API_KEY environment variable not set.")
                    print("Please set your OpenAI API key:")
                    print("1. Set OPENAI_API_KEY environment variable, or")
                    print("2. Create a .env file with: OPENAI_API_KEY=your_key_here")
                    print("3. Make sure the .env file is in the project root directory")
                    return
                else:
                    log_message(f"✅ OpenAI API key found (length: {len(api_key)} characters)")
                    
            except ImportError:
                print("❌ Error: OpenAI package not found.")
                print("Please install it with: pip install openai python-dotenv")
                return
        else:
            # Check if deep_translator is installed for Google Translate
            try:
                from deep_translator import GoogleTranslator
            except ImportError:
                print("Error: deep_translator package is required for translation.")
                print("Please install it with: pip install deep_translator")
                return
        
        # Create settings with CLI arguments
        settings = Settings(cli_args=args)
        
        file_manager = FileManager(settings)
        
        file_manager.create_needed_folders()
        
        
        # Get list of JAR files
        mod_list = os.listdir(settings.mods_path)
        jar_files = [m for m in mod_list if m.endswith(JAR)]
        
        if not jar_files:
            log_message("No JAR files found to translate")
            return
        
        log_message(f"Found {len(jar_files)} mod(s) to process\n")
        
        # Disable logging while progress bar is active to avoid overlap
        global DISABLE_LOGS
        original_disable = DISABLE_LOGS
        DISABLE_LOGS = True
        
        # Use a clean console before showing the progress bar
        print()
        
        progress = ThreadSafeProgress()
        progress.start("Processing mods")
        task_id = progress.add_task("Mods", total=len(jar_files))
        
        try:
            processed = 0
            skipped = 0
            failed = 0
            skip_messages = []
            
            for jar_file in jar_files:
                try:
                    result = file_manager.process_single_jar(jar_file)
                    if isinstance(result, tuple):
                        success, mod_name, status = result
                        if success:
                            if status == "processed":
                                processed += 1
                            elif status == "already_translated":
                                skipped += 1
                                skip_messages.append(f"⏭️ Skipping {mod_name} (already translated)")
                            elif status == "no_language_files":
                                skipped += 1
                                skip_messages.append(f"⏭️ No language files found in {mod_name}")
                        else:
                            failed += 1
                    else:
                        # Backward compatibility: if it returns a boolean
                        if result:
                            processed += 1
                        else:
                            failed += 1
                except Exception as e:
                    progress.stop()
                    DISABLE_LOGS = original_disable
                    log_message(f"❌ Failed to process {jar_file}: {str(e)}")
                    progress.start("Processing mods")
                    DISABLE_LOGS = True
                    failed += 1
                finally:
                    progress.update(task_id, advance=1)
        finally:
            progress.stop()
            DISABLE_LOGS = original_disable
            print()  # Add blank line to prevent overlap with progress bar
        
        # Display skip messages after progress bar stops
        for msg in skip_messages:
            log_message(msg)
        
        # Summary
        log_title('Translation Complete!')
        log_message(f"✅ Processed: {processed} mod(s)")
        log_message(f"⏭️ Skipped: {skipped} mod(s) (already translated)")
        if failed > 0:
            log_message(f"❌ Failed: {failed} mod(s)")
        
        # Clean up temp folder
        if os.path.exists(settings.temp_path):
            shutil.rmtree(settings.temp_path)
        
        # Only show success message if at least one mod was processed
        if processed > 0:
            log_title('All mods have been translated!\n')
        elif skipped > 0:
            log_title('All mods were already translated!\n')
        else:
            log_title('Translation process complete!\n')
    except Exception as e:
        print(f"Error translating mods: {e}")
        import traceback
        traceback.print_exc()
