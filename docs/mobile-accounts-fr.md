# Compte et notifications mobiles

Cette évolution nécessite la mise à jour de gestion du compte postérieure à
2.0.0. Sur un ancien serveur, la connexion et le monitoring restent disponibles ;
les nouvelles routes renvoient 404. L’authentification SQLite reste optionnelle.
La démonstration publique en lecture seule utilise un proxy distinct ; ne rendez
jamais un agent privé anonyme pour obtenir une démo.

Le compte permet de modifier le nom affiché et la langue (`system`, `fr`, `en`).
L’identifiant et le rôle restent administrés côté serveur. Un changement de mot
de passe exige le mot de passe actuel et la protection CSRF ; il conserve les
droits, mais révoque toutes les sessions, tous les jetons API et toutes les
inscriptions aux notifications. Recréez volontairement les jetons machine utiles.

## Mot de passe oublié

L’administrateur vérifie l’identité du demandeur par un canal de confiance puis
exécute sur le serveur :

```sh
monit-docker auth recovery-code --database /private/auth.db --username alice --output-file /private/recovery-code
```

Il transmet le code confidentiellement à l’utilisateur, qui le saisit avec son
nouveau mot de passe dans l’application. Le code est valable 15 minutes, à usage
unique ; seul son condensat est conservé. Un nouveau code remplace le précédent.
Supprimez le fichier après transmission. Aucun e-mail n’est envoyé : un identifiant au format e-mail ne constitue pas une adresse vérifiée et aucun serveur SMTP n’est configuré. Les comptes désactivés
ne peuvent pas récupérer leur accès. Les limites de tentatives persistent après
redémarrage. Le mot de passe doit comporter au moins 12 caractères.

## Notifications application fermée

Activez `serve --mobile-push`, ou `serve.mobile_push: true`, avec une base de comptes
et une origine HTTPS. Le serveur doit joindre `https://exp.host`. L’APK doit être
compilée avec la configuration Expo/Firebase correspondante et le téléphone doit
autoriser les notifications. Une APK sans cette configuration le signale.

L’inscription est volontaire, valable sept jours, renouvelable, avec cinq appareils
maximum par compte. La déconnexion en ligne et la révocation d’une session
suppriment les inscriptions liées. Un changement de rôle/mot de passe les supprime
toutes. Hors connexion, la révocation distante reste incertaine : révoquez ensuite
l’ancienne session ou attendez l’expiration de l’inscription. L’expiration d’une
session impose une reconnexion mais ne raccourcit pas silencieusement le
consentement de sept jours aux notifications.

Un travailleur séparé vérifie toutes les 30 secondes les observations du serveur :
monitoring indisponible, conteneur défaillant, arrêté ou en redémarrage, hors
maintenance. Les épisodes sont regroupés et les échecs espacés de cinq minutes.
Ces critères sont distincts des filtres et masquages locaux. Expo/Google reçoivent
le jeton, une notification générique traduite et l’origine du serveur, sans nom de
conteneur ni mesure. Toucher la notification ouvre l’état actuel authentifié et
n’exécute aucune action. Les alertes existantes continuent à fonctionner.

Le suivi distingue inscription, acceptation par le fournisseur et reçu de
transmission à Google/Apple. Le reçu est vérifié après 15 minutes ; il ne prouve
pas l’affichage sur le téléphone. L’arrêt forcé Android bloque la réception jusqu’à
réouverture ; réseau, batterie et permissions influencent aussi la réception.

Les sessions expirent après huit heures maximum ou 30 minutes d’inactivité.
L’activité renouvelle le délai d’inactivité dans cette limite. Aucun jeton de
renouvellement ni appairage QR n’est implémenté. Voir le
[contrat des routes API](mobile-accounts.md#api-additions).
