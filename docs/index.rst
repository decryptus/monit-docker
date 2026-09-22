monit-docker: choose your mode
==============================

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
   cron

.. toctree::
   :maxdepth: 2
   :caption: Serve mode

   serve
   compose
   metrics
   alerts
   notifications
   redis-notifications
   dwho-http-notifications
   grafana

.. toctree::
   :maxdepth: 1
   :caption: Help and development

   check-config
   troubleshooting
   architecture
   dockerhub
   pypi
