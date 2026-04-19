# Guide Rapide - Fonctionnalité de Reprise

## TL;DR (Résumé Court)

Vous pouvez maintenant **reprendre une traduction Minecraft interrompue** au lieu de la recommencer du zéro.

## Utilisation Rapide

### Mode Graphique (Recommandé pour les utilisateurs)

```bash
python -m app.commands.app
```

L'application détecte automatiquement si vous aviez commencé une traduction. Si oui, elle vous demande:
- **Resume previous translation** → Continue depuis où vous avez arrêté
- **Start fresh translation** → Recommence complètement

### Mode Ligne de Commande

Reprendre une traduction:
```bash
python -m app.commands.translate --resume -p ./mods -s en_US -t fr_FR
```

Recommencer:
```bash
python -m app.commands.translate -p ./mods -s en_US -t fr_FR
```

## Quand ça s'affiche?

La question "Resume or Fresh?" s'affiche quand:
- ✅ Un dossier `temp` existe et contient des fichiers
- ✅ Ces fichiers sont des traductions partielles (JSON/LANG)

La question ne s'affiche pas quand:
- ❌ Aucun dossier temp n'existe
- ❌ Le dossier temp est complètement vide

## Bénéfices

| Situation | Avant | Après |
|-----------|-------|-------|
| Interruption réseau à 90% | Recommencer (perte) | Reprendre (rapide) |
| Traduction de 100 mods | 30 min complète | 3 min reprise |
| Ajout d'une langue | Tout retraiter | Juste la nouvelle |

## Fichiers Importants

- 📄 `RESUME_FEATURE.md` - Documentation complète
- 📄 `TEST_RESUME_FEATURE.md` - Comment tester
- 📄 `IMPLEMENTATION_SUMMARY.md` - Détails techniques

## Troubleshooting Rapide

| Problème | Solution |
|----------|----------|
| Pas d'option de reprise | Vérifiez que `temp/` existe et n'est pas vide |
| Erreur lors de la reprise | Sélectionnez "Start fresh translation" |
| Fichiers JAR corrompus | Restaurez les originaux et recommencez |

## Questions Fréquentes

**Q: Que se passe-t-il si je sélectionne "Resume" mais que les fichiers sont corrompus?**
R: L'application détecte l'erreur et bascule automatiquement sur un nouveau démarrage.

**Q: Puis-je reprendre si j'ai changé la langue cible?**
R: Oui, cela fonctionnera, mais ce ne sera peut-être pas optimal. Utilisez plutôt "Start fresh translation".

**Q: Le dossier `temp` est-il nettoyé après?**
R: Oui, toujours. C'est sûr de relancer l'application après une traduction complète.

**Q: Puis-je réduire davantage le temps de traduction?**
R: En utilisant OpenAI (--ai) pour des traductions plus rapides et OpenAI cache pour les résultats ultérieurs.

---

💡 **Pour plus de détails**, consultez les fichiers de documentation dans le projet.
