monit-docker user documentation
===============================

Monitor Docker containers and optionally execute rules when conditions match.
Both modes use the same selectors and rule syntax.

* **Simple mode:** run a check, read statistics or execute a rule, then exit.
  Optionally schedule it with cron. No HTTP server or monitoring stack is needed.
* **Serve mode:** keep monitoring and expose cached status and metrics over HTTP.
  Optionally add Prometheus for history and Grafana for charts.

For installation and the command reference, see the
`project README <https://github.com/decryptus/monit-docker#installation>`_.

.. toctree::
   :maxdepth: 2
   :caption: Simple mode

   simple
   scenarios
   cron
   trigger-delay
   filesystems
   access-checks
   healthchecks
   runtime-checks
   restart-limit
   maintenance

.. toctree::
   :maxdepth: 2
   :caption: Serve mode

   serve
   http-api-contract
   ui
   compose
   metrics
   alerts
   notifications
   audit
   journal-compatibility
   audit-migration
   redis-notifications
   dwho-http-notifications
   grafana

.. toctree::
   :maxdepth: 1
   :caption: Reference, upgrades and troubleshooting

   check-config
   configuration-validation
   config-cli-contract
   terminal
   supported-environments
   installation-upgrades
   deprecation-policy
   troubleshooting
   release-1.0.1
   release-1.0.0

Contributor documentation
-------------------------

Changing the project? Use the separate :doc:`contributors` guide.

.. toctree::
   :maxdepth: 1
   :caption: For contributors

   contributors
