# Vérification du projet

Vérification réalisée le 8 octobre 2026.

Complément du 9 octobre 2026 : erreur de largeur des tableaux reproduite avec le Streamlit global 1.43.2, puis corrigée. Les aperçus d’import, tableaux et éditeurs CRM et contacts ont été contrôlés avec 1.43.2 et 1.65.0. Le parcours complet et les 29 tests passent avec la 1.65.0 du projet. Le lanceur vérifie la version de Streamlit dans `.venv` avant le démarrage.

## Vérifications réalisées

- Tests unitaires et d’intégration sur la logique métier, les imports et les exports.
- Lecture du fichier SIRHA réel : 117 exposants, absence de colonne stand signalée.
- Lecture du SIGEP réel : 20 entreprises, 32 lignes et contacts multiples conservés.
- Parcours Streamlit testé avec AppTest : pages, import d’exemple, création de campagne, ajout d’une entreprise CRM, validation manuelle d’une cible puis exports Excel, Word, PDF et ZIP.
- Contrats OpenAI simulés : recherche web suivie d’extraction JSON, citations absentes, contradiction, identité ambiguë, email supposé et mauvaise édition du stand.
- Traitements de fond simulés : résultats persistés, champs déjà validés conservés, arrêt et erreur API bloquante.
- Interface consultée dans le navigateur et serveur local répondant au contrôle de santé.
- Rendu PDF d’un échantillon contrôlé visuellement : stand visible, rubriques lisibles, absence de chevauchement.

La commande de référence est :

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

## Limites de la vérification

Les appels OpenAI réels n’ont pas été exécutés. La disponibilité du modèle, le quota, les coûts et la pertinence des recherches doivent être contrôlés avec la clé de l’utilisateur, en commençant par une seule entreprise.

Les documents Word sont testés structurellement et leur texte est relu automatiquement. Le rendu Word n’a pas pu être contrôlé avec le convertisseur fourni, car LibreOffice n’est pas disponible dans le runtime. Le rendu PDF utilise son propre moteur et ne constitue pas une validation du rendu Word.

Les exports suivent les données disponibles. Une information privée, absente ou ancienne ne devient pas certaine par génération IA. Une validation finale reste nécessaire avant exploitation commerciale.
