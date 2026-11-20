# Assistance aux incidents

Cette fonction facultative et open source est disponible depuis la **2.0.0**. La commande `monit-docker explain` explique la situation d'un conteneur
à partir des observations disponibles, avec Ollama, une API compatible OpenAI
ou l'API native Anthropic de Claude.
Elle ne nécessite ni compte de courtage ni licence produit supplémentaire.
L'utilisation d'une API distante est facturée par le fournisseur choisi.
La surveillance normale ne déclenche aucun appel au modèle.

La fonction propose une commande CLI et un parcours HTTP/web facultatif.
La qualité des explications et la compatibilité des modèles restent à qualifier
en conditions réelles. Les tests automatisés utilisent des réponses synthétiques.

L'adresse de l'API, le modèle et le chemin du fichier privé contenant la clé sont
configurables. Le fournisseur doit respecter le protocole Chat Completions
compatible OpenAI, celui d'Ollama ou l'API Messages d'Anthropic ; un autre
protocole nécessite un adaptateur.
Chaque modèle reste à tester. Les contributeurs peuvent comparer les fournisseurs
avec le [banc d'essai synthétique](incident-ai-review.md#reproducible-synthetic-evaluation).

## Vérifier les données avant l'envoi

Depuis le dépôt de la version 2.0.0 :

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/monit-docker explain --container-id "$CONTAINER_ID" --preview
```

`CONTAINER_ID` doit être l'identifiant complet : 64 caractères hexadécimaux
minuscules. La source par défaut est le statut de l'agent déjà lancé sur
`http://127.0.0.1:9808/v1/status`. La commande ne se connecte pas à Docker.
`--status-file status.json` permet aussi d'utiliser une réponse `/v1/status`
enregistrée. Pour essayer sans agent ni modèle :

```sh
.venv/bin/monit-docker explain \
  --status-file examples/ai/status.json \
  --container-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --preview --output json
```

L'horodatage fixe de cet exemple est volontairement ancien. La fraîcheur dépend
de l'horodatage de collecte et de l'âge déclaré : 90 secondes maximum par défaut,
modifiable avec `--max-age SECONDES` (1 à 86400). Une ancienne collecte conservée
après un échec reste historique. Changer la limite ne rafraîchit pas les données.

## Configurer le modèle

Copier un exemple de `examples/ai/` vers un fichier privé et remplacer `MODEL_ID`
par un modèle disponible sur le serveur choisi. Le YAML IA est séparé de la
configuration de surveillance, sans moteur de modèles ni imports.

Pour Ollama local :

```yaml
provider: ollama
endpoint: http://127.0.0.1:11434/api/chat
model: MODEL_ID
timeout: 30
max_output_tokens: 1024
```

Pour une API compatible OpenAI :

```yaml
provider: openai-compatible
endpoint: https://api.openai.com/v1/chat/completions
model: MODEL_ID
allow_remote: true
api_key_file: /run/secrets/monit-docker-ai-key
timeout: 30
max_output_tokens: 1024
```

Le modèle doit accepter Chat Completions sans streaming, le mode JSON
`response_format: {type: json_object}` et `max_completion_tokens`.
Une URL distante nécessite HTTPS et `allow_remote: true`. HTTP est limité aux
adresses IP de boucle locale : utiliser `127.0.0.1` ou `::1`, pas `localhost`.

Pour Claude directement chez Anthropic, utiliser `examples/ai/anthropic.yml` :

```yaml
provider: anthropic
endpoint: https://api.anthropic.com/v1/messages
model: MODEL_ID
allow_remote: true
api_key_file: /run/secrets/monit-docker-anthropic-key
timeout: 30
max_output_tokens: 1024
```

L'adaptateur utilise l'API Messages native, version `2026-10-21`, une
authentification Bearer et une clé API dédiée obligatoire, limitée à un seul
espace de travail. Les clés multi-espaces, l'authentification des plateformes
cloud et OAuth ne sont pas pris en charge. Choisir un modèle Claude accessible
au compte. Une instruction système demande du JSON, validé localement ; aucun
format structuré imposé par le fournisseur n'est demandé. Les réponses entourées
de Markdown, refus, sorties tronquées et blocs de réflexion ou d'outils sont
rejetés sans nouvelle tentative. Seul un bloc texte terminé par `end_turn` est
accepté. Le modèle réel reste à qualifier. Aucun outil, cache de prompt ni mode
de réflexion étendue n'est demandé. Si le fournisseur déclare malgré tout une
entrée mise en cache, son décompte est laissé inconnu pour éviter une estimation
de coût fondée sur un nombre incomplet de jetons.

Créer un fichier de clé privé, appartenant au compte exécutant la commande, avec
les permissions `0600`. Il contient uniquement la clé. Ne pas mettre la clé dans
le YAML ou la ligne de commande, ni la conserver dans Git. Un chemin relatif est
résolu depuis le dossier du fichier de configuration IA. Les liens symboliques
ne sont pas acceptés pour ce fichier.

```sh
.venv/bin/monit-docker explain --container-id "$CONTAINER_ID" \
  --ai-config /chemin/ai.local.yml
```

Un lancement effectue au maximum un appel, sans nouvelle tentative automatique.
`--preview` ne lit même pas le fichier de configuration IA ou la clé.
Les redirections et les variables de proxy sont ignorées. Le résultat fournit
la durée de la requête et les jetons déclarés par le fournisseur ; aucun coût
monétaire n'est estimé. Vérifier les tarifs du fournisseur avant utilisation.

## Périmètre des données et du résultat

Le modèle reçoit un alias (`container-1`), les mesures numériques autorisées,
l'état et la santé, les protections, la maintenance et le budget de redémarrage.
Les noms, identifiants réels, labels, variables d'environnement, chemins,
journaux bruts et contenus des commandes sont exclus. Les autres conteneurs
sont exclus. Ces mesures restent des informations d'exploitation : contrôler
l'aperçu avant un envoi distant. Le rapport local conserve l'identifiant réel.

Pour inclure un historique d'actions et décrire une règle :

```sh
.venv/bin/monit-docker --audit-file /chemin/audit.jsonl explain \
  --container-id "$CONTAINER_ID" --ai-config /chemin/ai.local.yml \
  --rule 'mem_percent > 90 ? restart'
```

L'historique comprend au maximum 20 actions correspondantes issues d'une seule
page bornée du journal, sans champs de texte libre. Un historique tronqué est
signalé. `--rule` accepte jusqu'à 10 règles explicites numériques/d'état/de santé,
sans alias configurés. Ces règles sont décrites, jamais exécutées ni déclarées
actives sans preuve. Le contenu des commandes personnalisées reste opaque.

Les faits sont assemblés localement ; les hypothèses du modèle et les contrôles
suggérés sont séparés et non vérifiés. Chaque référence doit correspondre à une
observation fournie, mais cela ne prouve pas la justesse du raisonnement.
Les données manquantes ou anciennes restent visibles. Aucune suggestion ne peut
déclencher une action, modifier la configuration ou contourner les protections.
Les explications peuvent être en français ou en anglais ; les codes et diagnostics serveur restent en anglais.

`--output json` produit un rapport structuré. Les erreurs opérationnelles renvoient
le code 120 et un champ `reason` stable, sans corps de réponse du fournisseur ni
clé. Les arguments CLI invalides utilisent le code 2 d'argparse.
La surveillance fonctionne indépendamment.

| Limite | Valeur |
| --- | --- |
| Statut lu sur disque ou HTTP | 2 Mio |
| Contexte envoyé | 24 000 octets |
| Réponse du fournisseur / contenu JSON du modèle | 65 536 / 16 000 octets |
| En-têtes / profondeur JSON | 16 384 octets / 64 niveaux |
| Délai d'une requête | 30 secondes ; configurable de 1 à 120 |
| Jetons de sortie demandés | 1 024 ; configurable de 128 à 2 048 |
| Éléments par liste / caractères par texte | 8 / 1 000 |

Le fournisseur reçoit la limite de jetons ; les limites de taille et de durée
sont appliquées localement. Les refus, appels d'outils, réponses incomplètes,
références inconnues et JSON invalides sont rejetés. libcurl doit prendre en
charge la résolution DNS asynchrone. Un modèle lent peut dépasser le délai ;
aucun basculement ni nouvel appel automatique n'est effectué.

[Guide anglais](incident-ai.md) · [Revue pour les contributeurs](incident-ai-review.md)

## Parcours web facultatif

Utiliser des compilations du code candidat pour **l'agent et l'interface** : les
images 2.0.0 contiennent cette fonction. Configurer les trois
options dans `serve:` (ou leurs équivalents CLI avec des tirets) :

```yaml
serve:
  ai_config: /private/incident-ai.yml
  ai_token_file: /private/ai-proxy-token
  ai_origin: https://monitor.example
  trust_proxy_user: true
```

Le fichier fournisseur est identique à celui de la commande CLI. Il reste sur
l'agent avec la clé privée. Le fichier `ai_token_file` contient un nouveau secret
hexadécimal de 64 caractères, différent des secrets de lecture, métriques,
journal, notifications et actions. Le générer localement avec
`openssl rand -hex 32` sous `umask 077`. Aucun secret ne va dans le navigateur.
L'accès IA ne nécessite pas d'activer les actions Docker ni l'accès HTTP au journal.

Pour l'interface Nginx, monter `examples/ai/nginx.conf` en lecture seule à
`/etc/nginx/monit-ai.conf`, ainsi que :

- `/run/secrets/ai-htpasswd`, fichier HTTP Basic réservé aux opérateurs IA. Ces
  opérateurs doivent aussi pouvoir accéder au tableau de bord ; conserver leurs
  mêmes identifiants dans les deux fichiers évite les demandes répétées.
- `/run/secrets/proxy-ai.conf`, contenant uniquement la directive
  `proxy_set_header X-Monit-AI-Token "<secret-hexadecimal-de-64-caracteres>";`.

`ai_origin` doit correspondre exactement à l'origine HTTPS publique. Le proxy
remplace l'identité et le secret fournis par le client, accepte uniquement POST,
ne réessaie pas l'appel et attend jusqu'à 130 secondes (120 au maximum côté
fournisseur). L'interface livrée par défaut n'expose aucune route IA. Le port de
l'agent reste privé. En conteneur, prévoir explicitement l'accès au fournisseur :
le réseau `agent` de l'exemple est interne, sans sortie Internet ; l'adresse
loopback d'Ollama désigne l'espace réseau de l'agent. La démo publique n'est pas activée.

Cliquer sur **Explain incident**, examiner les observations filtrées, puis sur
**Request explanation**. L'aperçu ne charge pas la clé et ne contacte aucun modèle.
Il peut inclure une page bornée du journal des actions du conteneur, avec mention
de l'historique incomplet. Les logs bruts et les définitions de règles actives ne
font pas partie du contexte web. Un journal inaccessible bloque la demande.

L'aperçu est lié à l'opérateur et au conteneur, conservé pendant 60 secondes, avec
au plus 32 aperçus en mémoire. Si la collecte, le journal ou la fraîcheur changent,
**Refresh preview** est obligatoire. Le modèle reçoit exactement l'aperçu validé,
avec son âge au moment de l'aperçu. Un seul appel peut être en cours, avec au moins
60 secondes entre deux départs, erreurs comprises, tous opérateurs confondus.
Ces limites sont par processus et repartent à zéro au redémarrage ; elles ne
remplacent pas un plafond de facturation chez le fournisseur.

L'interface sépare observations, hypothèses, données manquantes et vérifications
en lecture seule. Le texte du modèle est échappé et ne peut déclencher aucune
action Docker. La latence et les compteurs de jetons disponibles sont affichés.
Fermer la fenêtre n'annule pas un appel déjà accepté. Une erreur ou une réponse
perdue peut quand même être facturée ; aucun nouvel essai n'est automatique.
La surveillance et la planification des actions continuent indépendamment.

### Contrat HTTP

Les deux routes exigent JSON, `X-Monit-AI-Token`, l'origine exacte et, si activé,
`X-Monit-Actor` remplacé par le proxy. Taille maximale : 1 Kio ; champs inconnus
refusés. Le navigateur ne peut fournir ni modèle, ni clé, ni URL, ni prompt,
ni observations à transmettre.

| Route | Champs exacts | Réponse |
| --- | --- | --- |
| `POST /v1/ai/preview` | `container_id` | Aperçu, `preview_id`, `expires_in` |
| `POST /v1/ai/explain` | `container_id`, `preview_id` | Contexte, analyse, latence et compteurs disponibles |

`ai_enabled` dans `/v1/status` indique la présence de la fonction, pas un droit
d'accès. Codes : 404 si désactivée, 403 pour l'autorisation ou l'origine, 400 pour
une demande invalide, 409 pour un aperçu expiré/utilisé/modifié, 429 pour les
limites d'admission, 504 pour le délai fournisseur, 503 pour les autres erreurs
fournisseur/journal. Le champ `error` contient une raison stable. Toute tentative
atteignant le chargement du fournisseur consomme l'aperçu, même en cas d'échec.

## Langue des explications

Utiliser `--language fr` ou `--language en` avec `monit-docker explain` (anglais
par défaut). Le formulaire de connexion, la barre du compte et la fenêtre IA
partagent une préférence mémorisée. La langue du navigateur sert de choix initial,
avec repli en anglais. Les textes de connexion et d’IA utilisent i18next hébergé
localement et des catalogues séparés ; les autres vues ne sont pas encore traduites.

Les requêtes JSON d’aperçu et d’explication acceptent `language` (`fr` ou `en`).
L’aperçu est en anglais si ce champ est absent. L’explication reprend la langue de
l’aperçu si le champ est absent. Une valeur non prise en charge renvoie
`invalid_language` (400). Changer de langue après l’aperçu renvoie
`language_changed` (409), invalide cet aperçu et ne contacte aucun modèle.
L’interface prépare un nouvel aperçu ; l’envoi nécessite toujours une action explicite.

Les identifiants, clés JSON, références de preuve, valeurs brutes et codes d’erreur
restent inchangés. La consigne linguistique ne garantit pas la qualité du modèle.
Les essais avec de vrais modèles sont différés. Le banc d’essai accepte
`--language fr` et `--language en` et consigne la langue et l’empreinte du prompt.
