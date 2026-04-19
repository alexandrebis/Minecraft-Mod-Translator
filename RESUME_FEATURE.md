# Fonctionnalité de Reprise de Traduction (Resume Translation)

## Description

Cette fonctionnalité permet de reprendre une traduction Minecraft mod précédemment commencée mais non finalisée. Elle détecte automatiquement si un dossier `temp` contient déjà des fichiers de traduction pour une langue cible spécifique.

## Utilisation

### Via l'Interface Graphique (App Mode)

1. Lorsque vous lancez l'application avec un dossier `temp` existant contenant des traductions incomplètes:
   ```
   Minecraft Mod Translator > python -m app.commands.app
   ```

2. L'application détecte automatiquement la traduction incomplète et vous propose deux options:
   - **Resume previous translation** : Continue à partir des fichiers existants
   - **Start fresh translation** : Recommence une nouvelle traduction (supprime le dossier `temp`)

3. Le mode sélectionné s'affiche dans la table de confirmation

### Via la Ligne de Commande (CLI Mode)

Utiliser l'option `--resume`:

```bash
# Reprendre une traduction existante
python -m app.commands.translate --resume --path ./mods --source en_US --target es_ES

# Ou en utilisant la syntaxe courte
python -m app.commands.translate -p ./mods -s en_US -t es_ES --resume
```

## Détection Automatique

L'application détecte une traduction incomplète en cherchant les fichiers de la langue cible dans le dossier `temp`:
- Fichiers JSON: `{target_lang}.json` (ex: `es_ES.json`)
- Fichiers LANG: `{target_lang}.lang` (ex: `es_ES.lang`)

## Comportement

### Mode Reprise (Resume Mode)
- Les fichiers JAR **ne sont pas** re-dépaquetés
- Les fichiers de langue existants dans `temp` sont utilisés comme point de départ
- La traduction continue uniquement pour les éléments manquants/incomplets
- Plus rapide que de recommencer du zéro

### Mode Nouveau (Fresh Start)
- Le dossier `temp` existant est complètement supprimé
- Tous les fichiers JAR sont dépaquetés à nouveau
- Une nouvelle traduction complète est effectuée
- Les fichiers modifiés pendant une session précédente sont perdus

## Cas d'Usage

### Quand utiliser la reprise?
- La traduction a été interrompue par erreur réseau
- L'utilisateur veut ajouter une nouvelle langue cible
- La traduction a été partiellement complétée manuellement
- Vous avez modifié certains fichiers de traduction et vous voulez continuer

### Quand recommencer?
- Vous avez changé la langue source
- Vous voulez traduire vers une autre langue cible
- Les fichiers temporaires sont devenus corrompus ou obsolètes
- Vous voulez une traduction complètement nouvelle

## Implémentation Technique

### Fichiers Modifiés

1. **translate.py**
   - Nouvelle méthode `FileManager.check_incomplete_translation()`
   - Paramètre `resume` dans `Settings.__init__()`
   - Argument CLI `--resume` dans `add_translate_arguments()`
   - Logique de reprise dans `handle_translate_command()`

2. **app.py**
   - Détection du dossier `temp` dans `get_user_input()`
   - Proposition de reprise/recommencement à l'utilisateur
   - Affichage du mode dans la table de confirmation
   - Passage du flag `resume` à `handle_translate_command()`

### Flux de Exécution

```
1. Vérification du dossier temp
   ├─ Si vide/inexistant → procéder normalement
   └─ Si non-vide → demander à l'utilisateur
       ├─ Reprise → ignorer déballage
       └─ Nouveau → nettoyer et dépaqueter

2. Dépaquetage (si nécessaire)

3. Traduction

4. Conversion en fichiers JAR

5. Nettoyage du dossier temp
```

## Notes de Sécurité

- La reprise ne supprime pas les fichiers d'origine
- Le dossier `temp` est toujours nettoyé après la traduction complète
- Les fichiers JAR originaux sont sauvegardés avant modification
- La recommencement supprime complètement le `temp` existant

## Limitations

- La reprise fonctionne uniquement pour la langue cible détectée dans `temp`
- Impossible de reprendre si le dossier `temp` a été partiellement supprimé
- Les fichiers `.mcfunction` doivent être présents pour une reprise complète
