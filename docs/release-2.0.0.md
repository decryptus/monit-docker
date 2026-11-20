# Monit-Docker 2.0.0

Version 2.0.0 brings optional AI incident explanations and opt-in SQLite accounts
to the existing monitoring engine. Monitoring does not require an AI provider.

## Changes

- Explicit AI preview and explanation through CLI and web, using Ollama, the
  OpenAI-compatible API adapter or Anthropic Claude's API adapter. Provider usage
  may be billed. No model suggestion executes a command.
- French/English explanatory text (`--language fr|en`) and a remembered browser
  choice. Changing language invalidates the prior preview consent.
- SQLite accounts, roles, durable revocable browser/native sessions and scoped
  machine tokens. Activation remains explicit; existing legacy auth stays intact.
- Responsive branded sign-in with the official logo and French/English catalogs.

## Upgrade

Use matching agent/UI 2.0.0 images. Back up configuration, persistent state and
any existing SQLite database before updating. Enabling accounts is a separate
migration: follow the [authentication guide](authentication.md) and its
[French version](authentication-fr.md), including role mapping and rollback.
Keep secrets private. Review [AI setup](incident-ai.md) ([français](incident-ai-fr.md))
before enabling inference; it is disabled by default.

## Deferred verification and known limits

At the maintainer's request, real-model response quality/FR–EN review and mobile
API/device validation are deferred. Protocol adapters have automated synthetic
coverage, which does not certify every provider/model combination. No Android
release or device compatibility is claimed by this server/UI release. The
remaining dashboard views retain their existing English labels.
The public demo has separate visitor access; AI inference is not enabled there.

## Français

La 2.0.0 réunit l’IA facultative, les comptes SQLite activés explicitement, les
explications français/anglais et un formulaire de connexion avec logo. La mise à
jour seule n’active ni l’IA ni une migration des comptes. Utilisez les images
agent/interface 2.0.0 correspondantes et sauvegardez les données avant mise à jour.
Les essais manuels avec de vrais modèles et la validation/sortie mobile sont
différés à la demande du mainteneur. La démo publique doit rester accessible sans
connexion ; les installations avec comptes conservent leur authentification.
