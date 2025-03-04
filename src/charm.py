#!/usr/bin/env python3
# Copyright 2020 Canonical Ltd.
# See LICENSE file for licensing details.

import hashlib
import logging
import yaml
import json

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus
from ops.pebble import ConnectionError
from prometheus_provider import MonitoringProvider
from prometheus_server import Prometheus

PROMETHEUS_CONFIG = "/etc/prometheus/prometheus.yml"
logger = logging.getLogger(__name__)


class PrometheusCharm(CharmBase):
    """A Juju Charm for Prometheus."""

    _stored = StoredState()

    def __init__(self, *args):
        logger.debug("Initializing Charm")

        super().__init__(*args)

        self._stored.set_default(alertmanagers=[])
        self._stored.set_default(provider_ready=False)
        self._stored.set_default(prometheus_config_hash=None)

        self.framework.observe(self.on.prometheus_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(
            self.on["alertmanager"].relation_changed, self._on_alertmanager_changed
        )
        self.framework.observe(
            self.on["alertmanager"].relation_broken, self._on_alertmanager_broken
        )

        self.framework.observe(
            self.on["grafana-source"].relation_changed, self._on_grafana_changed
        )

        if self.provider_ready:
            self.prometheus_provider = MonitoringProvider(
                self, "monitoring", "prometheus", self.version
            )
            self.framework.observe(
                self.prometheus_provider.on.targets_changed,
                self._on_scrape_targets_changed,
            )

    def _on_pebble_ready(self, event):
        """Setup workload container configuration."""
        self._configure()

    def _on_config_changed(self, event):
        """Handle a configuration change."""
        self._configure()

    def _on_scrape_targets_changed(self, event):
        """Handle changes in scrape targets."""
        self._configure()

    def _configure(self):
        """Reconfigure and restart Prometheus.

        In response to any configuration change, such as a new consumer
        relation, or a new configuration set by the administrator, the
        Prometheus config file is regenerated, pushed to the workload
        container and Prometheus is restarted.
        """
        logger.info("Configuring Prometheus")
        container = self.unit.get_container("prometheus")

        # check if configuration file has changed and if so push the
        # new config file to the workload container
        prometheus_config = self._prometheus_config()
        config_hash = hashlib.md5(str(prometheus_config).encode("utf-8")).hexdigest()
        if self._stored.prometheus_config_hash != config_hash:
            try:
                container.push(PROMETHEUS_CONFIG, prometheus_config)
                self._stored.prometheus_config_hash = config_hash
                logger.info("Pushed new configuration")
            except ConnectionError:
                logger.info("Ignoring config changed since pebble is not ready")
                return

        # setup the workload (Prometheus) container and its services
        layer = self._prometheus_layer()
        plan = container.get_plan()
        if plan.services != layer["services"]:
            container.add_layer("prometheus", layer, combine=True)

            if container.get_service("prometheus").is_running():
                container.stop("prometheus")

            container.start("prometheus")
            logger.info("Prometheus started")

        if self.unit.is_leader():
            self.app.status = ActiveStatus()

        self.unit.status = ActiveStatus()

    def _on_stop(self, _):
        """Mark unit is inactive.

        All units of the charm are set to maintenance status before
        termination.
        """
        self.unit.status = MaintenanceStatus("Pod is terminating.")

    def _on_grafana_changed(self, event):
        """Provide Grafana with data source information.

        Grafana needs to know the port and name of an application in order
        to form a relation with it. Hence this information is provided here.
        """
        event.relation.data[self.unit]["port"] = str(self.model.config["port"])
        event.relation.data[self.unit]["source-type"] = "prometheus"
        event.relation.data[self.unit]["private-address"] = str(
            self.model.get_binding(event.relation).network.bind_address
        )

    def _on_alertmanager_changed(self, event):
        """Set an alertmanager configuration.

        In response to any changes in relations with Alertmanager,
        the list of currently available Alertmanagers is updated,
        a new Prometheus configuration set and Prometheus is
        restarted.
        """
        if not self.unit.is_leader():
            return

        addrs = json.loads(event.relation.data[event.app].get("addrs", "[]"))

        self._stored.alertmanagers = addrs

        self._configure()

    def _on_alertmanager_broken(self, event):
        """Remove all alertmanager configuration.

        When an Alertmanager departs it is removed from the list
        of currently available Alertmanagers, the Prometheus configuration
        is updated and Prometheus is restarted.
        """
        if not self.unit.is_leader():
            return
        self._stored.alertmanagers.clear()
        self._configure()

    def _command(self):
        """Construct command to launch Prometheus.

        Returns:
            a list consisting of Prometheus command and associated
            command line options.
        """
        command = ["/bin/prometheus"]
        command.extend(self._cli_args())

        return " ".join(command)

    def _cli_args(self):
        """Construct command line arguments for Prometheus.

        Returns:
            a list consisting of Prometheus command line options.
        """
        config = self.model.config
        args = [
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/var/lib/prometheus",
            "--web.enable-lifecycle",
            "--web.console.templates=/usr/share/prometheus/consoles",
            "--web.console.libraries=/usr/share/prometheus/console_libraries",
        ]

        # get log level
        allowed_log_levels = ["debug", "info", "warn", "error", "fatal"]
        if config.get("log-level"):
            log_level = config["log-level"].lower()
        else:
            log_level = "info"

        # If log level is invalid set it to debug
        if log_level not in allowed_log_levels:
            logging.error(
                "Invalid loglevel: {0} given, {1} allowed. "
                "defaulting to DEBUG loglevel.".format(
                    log_level, "/".join(allowed_log_levels)
                )
            )
            log_level = "debug"

        # set log level
        args.append("--log.level={0}".format(log_level))

        # Enable time series database compression
        if config.get("tsdb-wal-compression"):
            args.append("--storage.tsdb.wal-compression")

        # Set time series retention time
        if config.get("tsdb-retention-time") and self._is_valid_timespec(
            config["tsdb-retention-time"]
        ):
            args.append(
                "--storage.tsdb.retention.time={}".format(config["tsdb-retention-time"])
            )

        return args

    def _is_valid_timespec(self, timeval):
        """Is a time interval unit and value valid.

        Args:
            timeval: a string representing a time specification.

        Returns:
            True if time specification is valid and False otherwise.
        """
        if not timeval:
            return False

        time, unit = timeval[:-1], timeval[-1]

        if unit not in ["y", "w", "d", "h", "m", "s"]:
            logger.error("Invalid unit {} in time spec".format(unit))
            return False

        try:
            int(time)
        except ValueError:
            logger.error("Can not convert time {} to integer".format(time))
            return False

        if not int(time) > 0:
            logger.error("Expected positive time spec but got {}".format(time))
            return False

        return True

    def _are_valid_labels(self, json_data):
        """Are Prometheus external labels valid.

        Args:
            json_data: a JSON encoded string of external labels form
                Prometheus.

        Returns:
            True if external labels are valid, False otherwise.
        """
        if not json_data:
            return False

        try:
            labels = json.loads(json_data)
        except (ValueError, TypeError):
            logger.error("Can not parse external labels : {}".format(json_data))
            return False

        if not isinstance(labels, dict):
            logger.error("Expected label dictionary but got : {}".format(labels))
            return False

        for key, value in labels.items():
            if not isinstance(key, str) or not isinstance(value, str):
                logger.error("External label keys/values must be strings")
                return False

        return True

    def _external_labels(self):
        """Extract external labels for Prometheus from configuration.

        Returns:
            a dictionary of external lables for Prometheus configuration.
        """
        config = self.model.config
        labels = {}

        if config.get("external-labels") and self._are_valid_labels(
            config["external-labels"]
        ):
            labels = json.loads(config["external-labels"])

        return labels

    def _prometheus_global_config(self):
        """Construct Prometheus global configuration.

        Returns:
            a dictionary consisting of global configuration for Prometheus.
        """
        config = self.model.config
        global_config = {}

        labels = self._external_labels()
        if labels:
            global_config["external_labels"] = labels

        if config.get("scrape-interval") and self._is_valid_timespec(
            config["scrape-interval"]
        ):
            global_config["scrape_interval"] = config["scrape-interval"]

        if config.get("scrape-timeout") and self._is_valid_timespec(
            config["scrape-timeout"]
        ):
            global_config["scrape_timeout"] = config["scrape-timeout"]

        if config.get("evaluation-interval") and self._is_valid_timespec(
            config["evaluation-interval"]
        ):
            global_config["evaluation_interval"] = config["evaluation-interval"]

        return global_config

    def _alerting_config(self):
        """Construct Prometheus altering configuration.

        Returns:
            a dictionary consisting of the alerting configuration for Prometheus.
        """
        alerting_config = ""

        if len(self._stored.alertmanagers) < 1:
            logger.debug("No alertmanagers available")
            return alerting_config

        targets = [manager for manager in self._stored.alertmanagers]
        manager_config = {"static_configs": [{"targets": targets}]}
        alerting_config = {"alertmanagers": [manager_config]}

        return alerting_config

    def _prometheus_config(self):
        """Construct Prometheus configuration.

        Returns:
            Prometheus config file in YAML (string) format.
        """
        config = self.model.config

        scrape_config = {
            "global": self._prometheus_global_config(),
            "scrape_configs": [],
        }

        alerting_config = self._alerting_config()
        if alerting_config:
            scrape_config["alerting"] = alerting_config

        # By default only monitor prometheus server itself
        default_config = {
            "job_name": "prometheus",
            "scrape_interval": "5s",
            "scrape_timeout": "5s",
            "metrics_path": "/metrics",
            "honor_timestamps": True,
            "scheme": "http",
            "static_configs": [{"targets": ["localhost:{}".format(config["port"])]}],
        }
        scrape_config["scrape_configs"].append(default_config)
        if self._stored.provider_ready:
            scrape_jobs = self.prometheus_provider.jobs()
            for job in scrape_jobs:
                scrape_config["scrape_configs"].append(job)

        logger.debug("Prometheus config : {}".format(scrape_config))

        return yaml.dump(scrape_config)

    def _prometheus_layer(self):
        """Construct the pebble layer

        Returns:
            a dictionary consisting of the Pebble layer specification
            for the Prometheus workload container.
        """
        logger.debug("Building pebble layer")
        layer = {
            "summary": "Prometheus layer",
            "description": "Pebble layer configuration for Prometheus",
            "services": {
                "prometheus": {
                    "override": "replace",
                    "summary": "prometheus daemon",
                    "command": self._command(),
                    "startup": "enabled",
                }
            },
        }

        return layer

    @property
    def version(self):
        """Fetch Prometheus version.

        Returns:
            a string consisting of the Prometheus version information or
            None if Prometheus server is not reachable.
        """
        prometheus = Prometheus("localhost", str(self.model.config["port"]))
        info = prometheus.build_info()
        if info:
            return info.get("version", None)
        return None

    @property
    def provider_ready(self):
        """Check status of Prometheus server.

        Status of the Prometheus services is checked by querying
        Prometheus for its version information. If Prometheus responds
        with valid information, its status is recorded.

        Returns:
            True if Prometheus is ready, False otherwise
        """
        provided = {"prometheus": self.version}
        if not self._stored.provider_ready and provided["prometheus"]:
            logger.debug("Prometheus provider is available")
            logger.debug("Providing : {}".format(provided))
            self._stored.provider_ready = True

        return self._stored.provider_ready


if __name__ == "__main__":
    main(PrometheusCharm)
