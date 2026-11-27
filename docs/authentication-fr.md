# Authentification SQLite et migration

Disponible depuis la **2.0.0**. Utiliser des images agent et interface de même version, 2.0.0 ou ultérieure (version actuelle : 2.0.3). L'activation est explicite : une mise à jour seule ne change pas l'authentification existante. Le [guide technique anglais](authentication.md) décrit les commandes et le contrat API complet.

## Identifiants e-mail pour les nouveaux comptes

La création par CLI ou API exige un identifiant au format e-mail ASCII (partie
locale sans guillemets, domaine DNS avec un point, 254 caractères maximum). Les
adresses avec `+` sont acceptées. La casse est conservée : utilisez exactement
la même écriture à la connexion. Seule la syntaxe est vérifiée ; cela ne valide
ni la propriété de la boîte ni la récupération du mot de passe par e-mail.

Les anciens comptes conservent identifiant, mot de passe, rôles et sessions.
Leur réinitialisation administrative et l’import explicite htpasswd restent
possibles. Au démarrage du serveur, `auth_accounts_without_email` signale une fois
le nombre de comptes sans identifiant e-mail, comptes désactivés compris, sans
afficher les identifiants ou les secrets. Les connexions et actualisations ne
répètent pas cet avertissement.

## Comptes, droits et sessions

Les comptes, empreintes de mots de passe, sessions, empreintes des jetons API et limites de connexion sont stockés dans une base SQLite locale, sans Redis ni service hébergé. Le fichier appartient à l'utilisateur du service, en `0600`, dans un dossier privé `0700`. En conteneur, monter durablement le **dossier**. Ne pas exposer directement le port de l'agent à Internet.

| Rôle | Droits sur l'instance entière |
| --- | --- |
| `viewer` | Lecture de l'état des conteneurs |
| `operator` | Lecture, journal, actions manuelles et assistance IA si activées |
| `admin` | Droits opérateur, gestion des comptes et métriques |
| `metrics` | Métriques uniquement, pour un compte technique |
| `receiver` | Réception des notifications uniquement, pour un compte technique |

Le serveur vérifie les droits. Aucun droit ne déclenche à lui seul l'activation des actions ou de l'IA. Il n'y a pas encore de droits par conteneur ni d'écran web de gestion des comptes. L'administration reste possible par la CLI et par l'API administrateur.

Exécuter sous l'utilisateur du service :

```sh
install -d -m 700 /var/lib/monit-docker/auth
monit-docker auth user-add --database /var/lib/monit-docker/auth/auth.db --username administrateur@example.com --role admin
monit-docker serve --auth-db /var/lib/monit-docker/auth/auth.db --auth-origin https://monitor.example
```

La commande demande le mot de passe sans l'afficher : 12 caractères minimum, 1024 octets UTF-8 maximum. Pour un compte existant, `user-add` remplace le mot de passe et le rôle, réactive le compte et révoque toutes ses sessions et ses jetons API. `auth role` et `auth disable` révoquent également les accès existants. Toujours préciser `--database`, puis `--username` et éventuellement `--role`. La CLI locale permet de récupérer l'accès si le dernier administrateur a été désactivé.

## Connexion web et APK

Remplacer explicitement la configuration Basic du proxy par `ui/nginx/sqlite.conf`, montée dans `/etc/nginx/conf.d/default.conf`. L'image UI correspondante inclut `monit-sqlite-proxy.conf`. Conserver les certificats TLS et le réseau privé vers `monit-docker:9808`. L'origine HTTPS publique, port compris, doit correspondre exactement à `--auth-origin`. Ne pas ajouter de Basic Auth ou de CORS sur ce proxy.

La connexion web utilise un cookie `Secure`, `HttpOnly`, `SameSite=Strict`. Seul le jeton CSRF est conservé dans le `sessionStorage` de l'onglet ; le mot de passe ne l'est jamais. Un nouvel onglet sans jeton CSRF demande une connexion. Une nouvelle connexion remplace la session précédente du même navigateur. La déconnexion révoque la session côté serveur ; un échec réseau est signalé.

Les sessions durent au maximum 8 heures et expirent après 30 minutes sans requête authentifiée. Le rafraîchissement automatique compte comme activité. Elles survivent au redémarrage du service. Un changement de rôle ou de mot de passe, ou une désactivation, les révoque. Les nouveaux mots de passe utilisent Argon2id. Une base indisponible renvoie `503`, sans accès anonyme de secours. Les tentatives sont limitées par compte, pair réseau et globalement ; les clients derrière le même proxy partagent sa limite réseau.

L'APK actuel utilisant Basic doit être adapté **avant la bascule**. Le nouveau protocole est : découverte `GET /v1/auth/info`, connexion `POST /v1/auth/login` avec `X-Monit-Client: native-v1`, puis `Authorization: Session <jeton>` et `X-CSRF-Token` pour les modifications. Stocker les jetons dans le stockage sécurisé du téléphone, sans conserver le mot de passe. La déconnexion est un `POST /v1/auth/logout` avec `{}`. Aucun repli automatique vers l'ancien Basic ou la démo publique.

Activer séparément `--auth-audit --audit-file CHEMIN`, `--auth-notifications` ou les actions avec `--allow-actions --state-file CHEMIN` selon le besoin. Les règles et protections existantes restent applicables.

## Migration et bascule

1. Inventorier **tous** les fichiers htpasswd et leurs règles d'accès, y compris les comptes séparés pour le mobile, le journal ou l'IA. L'import traite un fichier complet : consolider plusieurs fichiers hors ligne en résolvant explicitement les doublons. Geler les modifications de comptes pendant l'opération.
2. Sauvegarder en privé les configurations du service, de Compose, du proxy et des clients, les fichiers htpasswd et les anciens jetons. L'outil sauvegarde son fichier source et la correspondance des rôles ; il ne sauvegarde pas automatiquement la configuration du déploiement.
3. Créer un fichier JSON privé avec un rôle explicite pour chaque compte, par exemple `{"alice":"viewer","operateur":"operator"}`. Aucun administrateur n'est attribué par défaut. Les noms commencent par une lettre ou un chiffre ASCII, puis acceptent lettres/chiffres/`.`/`_`/`-`, sur 64 caractères maximum. Renommer explicitement les anciens noms incompatibles et adapter les clients.
4. Faire une simulation sans écriture, puis appliquer avec un **nouveau** dossier de sauvegarde :

```sh
monit-docker auth migrate --database /var/lib/monit-docker/auth/auth.db --htpasswd /private/users.htpasswd --roles-file /private/roles.json
monit-docker auth migrate --database /var/lib/monit-docker/auth/auth.db --htpasswd /private/users.htpasswd --roles-file /private/roles.json --backup-dir /private/auth-backup-001 --apply
```

L'import valide tout le fichier, crée des sauvegardes privées avec empreintes SHA-256, synchronise leurs fichiers et entrées de dossiers sur disque, écrit dans une base temporaire en une transaction, vérifie le nombre de comptes et l'intégrité, puis publie la base sans écraser de fichier existant. Les sources sont revérifiées avant publication et restent inchangées. Après échec, examiner le résultat ; ne pas supprimer arbitrairement une base existante pour relancer.

SHA-256/512 crypt et bcrypt à coût borné sont acceptés. APR1, SHA-1, DES, texte clair, empreinte invalide ou coût excessif font échouer l'import entier. Réinitialiser explicitement les comptes incompatibles avant migration. Un ancien mot de passe compatible est conservé ; son empreinte passe à Argon2id après connexion réussie s'il atteint la longueur minimale. Un mot de passe ancien trop court nécessite une réinitialisation. Basic n'a pas de session serveur à reprendre : les utilisateurs devront se reconnecter.

5. Créer un administrateur s'il manque. Les anciens secrets partagés ne représentent pas des comptes : ils ne sont **pas importés**. Émettre des jetons API `Bearer` avec droits explicites et reconfigurer Prometheus et les récepteurs. Exemple après création d'un compte `prometheus` de rôle `metrics` :

```sh
monit-docker auth token --database /var/lib/monit-docker/auth/auth.db --username prometheus --scope metrics:read --ttl 86400 --output-file /private/prometheus-token
```

Le fichier doit être nouveau et sera créé en `0600`. Prévoir le renouvellement : durée de 1 seconde à 30 jours, 1 jour par défaut. Les anciennes options de secrets proxy sont incompatibles avec `--auth-db`.

6. Valider sur une instance privée les connexions valides/invalides, le refus des actions au lecteur, les droits administrateur, la révocation et la persistance après redémarrage. Ensuite seulement, arrêter l'ancienne instance et basculer ensemble le service et le proxy. Les données de supervision, d'actions et d'audit restent séparées de la base d'authentification.

## Retour arrière

Arrêter **tous** les processus qui écrivent dans la base. Restaurer les anciennes configurations du service, du proxy et des clients avec leurs protections. Avant redémarrage, il est possible de retirer la base migrée du chemin actif :

```sh
monit-docker auth rollback --database /var/lib/monit-docker/auth/auth.db --backup-dir /private/auth-backup-001 --writers-stopped
```

La commande vérifie la sauvegarde et le schéma, puis renomme la base en `.retired-*`. Elle conserve les comptes et sessions créés depuis la bascule ; elle ne les recopie pas dans htpasswd et ne restaure pas la configuration. Réconcilier les désactivations et changements de mot de passe avant de réactiver les anciens accès. Prévoir la version APK adaptée au mode restauré.

Pour les sauvegardes courantes, arrêter les écritures ou utiliser l'API de sauvegarde SQLite : ne pas copier uniquement le fichier d'une base active. Protéger les sauvegardes, qui contiennent des empreintes de mots de passe et de jetons. Un schéma inconnu est refusé ; ne pas rétrograder une base active en place.

## Langue de la connexion web

Le formulaire avec le logo propose le français et l’anglais avant connexion et dans la barre du compte. La langue du navigateur sert de choix initial ; la préférence explicite est mémorisée localement, avec l’anglais en repli. Aucun mot de passe n’est enregistré par ce sélecteur. Ce choix pilote aussi les explications IA ; les autres vues du tableau de bord conservent leur langue actuelle. L’accès public de la démo se configure séparément des installations avec comptes.

See [account lifecycle and remote notifications](mobile-accounts-fr.md).
