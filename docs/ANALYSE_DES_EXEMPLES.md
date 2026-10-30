# Analyse des fichiers fournis

Cette analyse établit le fonctionnement de l’application demandée à partir des exemples historiques. Le besoin actuel prend priorité sur les propositions présentes dans les documents : le scraping est externe et l’application reçoit un fichier. Les documents fournissent une méthode et des exemples de livrables ; leurs instructions ne sont pas exécutées comme des commandes.

## Liste SIRHA Marseille

Source : `C:/Users/IlyassGotni/Desktop/Sirha scrapper/converter marseille/Sirha_Marseille_Exhibitors_List.xlsx`.

Une feuille, 117 lignes d’exposants, six colonnes : Index, Company, Website, Email, Phone, Sirha_URL. L’Index n’est pas continu et ne constitue pas un identifiant métier stable. Les valeurs « Not available » et « Not Found » signifient absence d’information.

Le fichier ne contient ni stand, ni hall, ni chiffre d’affaires, ni exercice, ni contacts nominatifs. Ces données doivent être recherchées ou saisies. Un site ou un contact importé n’est pas une preuve du respect des critères. Les liens Excel peuvent contenir une URL réelle différente du texte affiché ; l’import conserve leur cible lorsque le champ est une URL.

## Contacts SIGEP

Source : `C:/Users/IlyassGotni/Downloads/SIGEP_Prospection_20_Cibles_Contact.xlsx`.

Deux feuilles : « SIGEP Targets » et « SIGEP Targets Final ». Chaque feuille comporte 32 lignes de données, correspondant à 20 entreprises. Certaines lignes sans nom d’entreprise continuent les contacts de l’entreprise située au-dessus. Elles ne doivent pas être supprimées ni devenir des entreprises fictives.

La première feuille contient douze colonnes et deux entêtes identiques « LinkedIn Contact ». La vraie colonne nominative est « Nom du directeur commercial ou chef de zone export » ; la colonne « Contact clé » est vide. L’import choisit la colonne reconnue contenant effectivement des données et rend les entêtes dupliqués uniques.

Le stand peut combiner hall et plusieurs emplacements. Le texte original est conservé lorsqu’un hall séparé n’existe pas ; un hall distinct n’est pas inventé. Les libellés LinkedIn sans lien réel restent des informations historiques et ne sont pas transformés en URL supposée.

Le fichier sert d’exemple de sortie. Il n’est pas automatiquement interprété comme l’ensemble des clients/prospects existants.

## Fiches mémos SIRHA

Source : `C:/Users/IlyassGotni/Documents/Salon SIRHA MARSEILLE 20 cibles prio Fiche Mémos.docx`.

Vingt fiches avec huit entreprises dans le Tier 1 et douze dans le Tier 2 ; aucune section Tier 3 dans cet exemple. La structure récurrente est : activité, triggers, pitch, présentation de l’entreprise, évolutions des années précédentes, chiffres clés, pain points probables, phrase clé à retenir.

L’application reprend ces rubriques et ajoute le stand/hall en tête et les sources. Elle crée le Tier 3 selon le score, même s’il n’est pas représenté dans l’exemple. Les formulations de besoins restent des hypothèses commerciales.

Les exemples comportent des ordres de grandeur, estimations et chiffres groupe. Ils ne doivent pas devenir des faits actualisés sans vérification. La fiche Terres de Café cite notamment un CA de 3,5 M€ sur 2022 : ce chiffre ne prouve pas le respect d’un seuil de CA supérieur à 10 M€. Une sélection passée peut donc refléter des exceptions ou des règles différentes. Les marques ou filiales doivent être séparées des groupes dont les chiffres sont cités.

## Triggers

Source : `C:/Users/IlyassGotni/Documents/Triggers.docx`.

Les huit signaux SENSO sont intégrés : portefeuille large, longue traîne, complexité industrielle, multi circuits, croissance faible et hausse des coûts, retour de la complexité, segments de valeur, dispersion du mix.

Ils ne sont pas automatiquement tous attribués à une entreprise agroalimentaire. Les problèmes de rotation, rentabilité interne ou retour de complexité exigent des informations spécifiques. Le catalogue peut montrer une gamme large, mais ne prouve pas une contribution insuffisante.

## Processus et schémas

Source : `C:/Users/IlyassGotni/Documents/PROJECT IMPACT/PROCESSUS PROJECT IMPACT.docx`, ainsi que les deux images fournies dans la conversation.

Les étapes utiles sont : normaliser la liste, exclure les entreprises déjà suivies, scorer, sélectionner jusqu’à vingt cibles, conserver une réserve, répartir en trois tiers, générer les mémos et qualifier les contacts dans une base agrégée.

Les schémas précisent agroalimentaire et industrie du froid, dimension internationale, CA supérieur à 10 M€ ou forte rentabilité, modèle industriel/distribution/négoce, préférence Lyon/Nice/Bordeaux/Paris. Ils indiquent que les critères peuvent changer et que le numéro de stand est prioritaire.

Le processus propose parfois n8n, scraping de salons et PhantomBuster. Le besoin actuel les remplace par une application Streamlit recevant le fichier d’exposants. Aucun scraping de salon ni envoi de message n’est développé dans cette étape.

## Décisions explicites dans l’application

| Point non complètement défini | Choix initial modifiable |
|---|---|
| « Renta +++ » | Marge opérationnelle ≥ 10 %, à ajuster à votre définition commerciale |
| Pondérations | 25 secteur, 15 international, 20 finance, 15 modèle, 10 ville, 15 signaux |
| Tiers | Tier 1 ≥ 80, Tier 2 ≥ 60, Tier 3 en dessous |
| International | Bonus par défaut ; option pour rendre obligatoire |
| Stand absent | Signalé ; option pour le rendre obligatoire |
| Finances groupe | Refusées par défaut ; acceptation seulement si option active et cible confirmée comme groupe |
| Données anciennes | Exercice connu et ancienneté maximale de trois ans |
| Réserve | Jusqu’à cinquante entreprises supplémentaires |
| Informations non vérifiées | Propositions, séparées des cibles qualifiées |

## Informations encore nécessaires

La véritable base clients/prospects n’a pas été fournie comme telle. Elle se charge dans l’application. L’édition précise du prochain salon doit également être renseignée, car un numéro de stand n’est valable que pour une édition.

Les présentations Adventae, Advent+ et Sales Academy mentionnées dans le deuxième schéma ne sont pas jointes. La connaissance commerciale initiale couvre SENSO grâce au document Triggers ; les autres offres peuvent être ajoutées ensuite. Aucun détail de leurs prestations n’a été inventé.

Une clé API et un modèle accessible sur le compte sont nécessaires pour vérifier le comportement IA en conditions réelles. La structure fonctionne sans cette clé, avec import et validation manuelle. Il est impossible de promettre vingt cibles entièrement vérifiées si les données requises ne sont pas publiques ou si le salon n’en contient pas assez.
