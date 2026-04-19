# Guide de Test - Fonctionnalité de Reprise de Traduction

## Configuration Requise

- Python 3.7+
- Packages: deep-translator, rich, questionary, pyfiglet
- Dossier `mods/` avec au moins un fichier MOD JAR pour tester

## Procédure de Test

### Test 1 : Interface Graphique avec Nouvelle Traduction

```bash
# 1. S'assurer que le dossier temp n'existe pas
rmdir /s temp 2>nul
del temp.zip 2>nul

# 2. Lancer l'application
python -m app.commands.app

# 3. Sélectionner les paramètres:
#    - Chemin: ./mods
#    - Langue source: en_US
#    - Langue cible: fr_FR
#    - Méthode: Google Translate
#    - Sortie: Remplacer les originaux

# 4. Confirmer
```

**Résultat attendu :**
- L'application demande les paramètres immédiatement
- Aucune option de reprise ne s'affiche
- La traduction commence normalement

### Test 2 : Interruption et Reprise Manuelle

```bash
# 1. Pendant une traduction en cours, appuyer sur Ctrl+C
#    (Le dossier temp reste avec des fichiers partiels)

# 2. Relancer l'application
python -m app.commands.app

# 3. Vérifier que l'application détecte la traduction incomplète
#    Une question s'affiche: "Do you want to resume the previous translation?"
```

**Résultat attendu :**
- Application détecte automatiquement le dossier `temp` non vide
- Deux options: "Resume previous translation" ou "Start fresh translation"
- Sélectionner "Resume" continue depuis le dossier temporaire
- La traduction complète se termine

### Test 3 : Recommencement Complet

```bash
# 1. Après un test de reprise, relancer l'application
python -m app.commands.app

# 2. Sélectionner "Start fresh translation"

# 3. Vérifier que le dossier temp est nettoyé
```

**Résultat attendu :**
- Le dossier `temp` existant est complètement supprimé
- Une nouvelle traduction complète commence
- Tous les fichiers JAR sont dépaquetés à nouveau

### Test 4 : Interface CLI avec --resume

```bash
# 1. Créer une traduction incomplète (arrêter avec Ctrl+C)

# 2. Reprendre via CLI
python -m app.commands.translate --resume -p ./mods -s en_US -t fr_FR

# 3. Vérifier que la traduction reprend
```

**Résultat attendu :**
- La traduction reprend sans redépaquetage
- Messages de log indiquent "Resuming previous translation"

### Test 5 : Détection Correcte de Langue Cible

```bash
# 1. Créer une traduction incomplète vers es_ES
python -m app.commands.app
#    Sélectionner es_ES comme langue cible
#    Interrompre avec Ctrl+C

# 2. Relancer et essayer de traduire vers pt_BR
python -m app.commands.app
#    Sélectionner pt_BR

# 3. Vérifier le comportement:
#    - Si reprise est sélectionnée vers une autre langue,
#      cela devrait toujours fonctionner
#    - Les fichiers es_ES existants ne doivent pas interférer
```

## Vérification Additionnelle

### Vérifier les Fichiers Créés

```bash
# Après une traduction complète, vérifier:
dir temp          # Doit être vide ou supprimé
dir mods          # Contient les fichiers JAR traduits
dir translated    # Contient les traductions (si sortie différente)
```

### Vérifier les Fichiers de Traduction

```bash
# Inspecter les fichiers JSON/LANG traduits:
# 1. Dépaqueter un JAR: unzip mod.jar
# 2. Chercher les fichiers de langue
# 3. Vérifier que les traductions sont présentes
```

## Cas de Test Spécifiques

### Test 6 : Dossier Temp Vide ou Invalide

```bash
# 1. Créer un dossier temp vide
mkdir temp

# 2. Lancer l'application
python -m app.commands.app

# 3. Vérifier qu'aucune option de reprise ne s'affiche
#    (Le dossier temp est considéré comme vide)
```

### Test 7 : Fichiers Temp Partiels

```bash
# 1. Créer une structure temp avec seulement des dossiers
mkdir temp\mod1\assets\lang

# 2. Lancer l'application
python -m app.commands.app

# 3. Vérifier la détection:
#    - Si aucun fichier de traduction, pas de reprise
#    - Si des fichiers fr_FR.json existent, propose la reprise
```

## Logs et Diagnostic

### Messages de Log Attendus

**Lors d'une détection:**
```
Found incomplete translation for fr_FR
Would you like to resume this translation? (y/n)
```

**Lors d'une reprise:**
```
Resuming previous translation...
Using existing unpacked mods in temp
Searching for language files in temp...
Translating mods...
```

**Lors d'un recommencement:**
```
Cleaning existing temp folder at temp
Unpacking mod files...
```

## Nettoyage Après les Tests

```bash
# Supprimer les fichiers temporaires
rmdir /s temp 2>nul
rmdir /s translated 2>nul
del test_*.log 2>nul

# Restaurer les fichiers MOD originaux si nécessaire
git checkout mods/ 2>nul
```

## Troubleshooting

### Problème: La reprise ne s'affiche pas

**Solutions:**
1. Vérifier que `temp/` existe et n'est pas vide
2. Vérifier qu'il contient des fichiers de traduction (ex: `fr_FR.json`)
3. Utiliser `ls -la temp/` pour inspecter le contenu
4. Activer le mode debug en ajoutant des print()

### Problème: Erreur lors de la reprise

**Solutions:**
1. Vérifier que la structure du dossier temp est intacte
2. Vérifier que les fichiers JSON/LANG ne sont pas corrompus
3. Essayer un "fresh start" pour recommencer
4. Vérifier l'espace disque disponible

### Problème: Fichiers JAR corrompus après reprise

**Solutions:**
1. Restaurer les originaux: `git checkout mods/`
2. Utiliser un nouveau dossier de traduction temporaire
3. Vérifier les permissions d'accès aux fichiers
