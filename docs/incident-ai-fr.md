# Assistance aux incidents (version de développement)

Cette fonction facultative et open source **n'est pas incluse dans la version
1.3.3**. La commande `monit-docker explain` explique la situation d'un conteneur
à partir des observations disponibles, avec Ollama ou une API compatible OpenAI.
Elle ne nécessite ni compte de courtage ni licence produit supplémentaire.
L'utilisation d'une API distante est facturée par le fournisseur choisi.
La surveillance normale ne déclenche aucun appel au modèle.

Cette première étape propose une commande CLI, sans bouton IA ni route HTTP IA.
La qualité des explications et la compatibilité des modèles restent à qualifier
en conditions réelles. Les tests automatisés utilisent des réponses synthétiques.

## Vérifier les données avant l'envoi

Depuis le dépôt contenant cette version de développement :

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
Les messages du produit et les explications sont en anglais.

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
