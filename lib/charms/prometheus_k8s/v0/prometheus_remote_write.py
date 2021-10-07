# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
"""This library facilitates the integration of the prometheus_remote_write interface.

Charms that need to push data to a charm exposing the Prometheus remote_write API,
should use the `PrometheusRemoteWriteConsumer`. Charms that operate software that exposes
the Prometheus remote_write API, that is, they can receive metrics data over remote_write,
should use the `PrometheusRemoteWriteProducer`.
"""

from typing import List, Optional, Union

from ops.charm import CharmBase, RelationMeta, RelationRole
from ops.framework import Object

DEFAULT_RELATION_NAME = "prometheus-remote-write"
RELATION_INTERFACE_NAME = "prometheus_remote_write"


class RelationNotFoundError(Exception):
    """Raised if there is no relation with the given name."""

    def __init__(self, relation_name: str):
        self.relation_name = relation_name
        self.message = f"No relation named '{relation_name}' found"

        super().__init__(self.message)


class RelationInterfaceMismatchError(Exception):
    """Raised if the relation with the given name has a different interface."""

    def __init__(
        self,
        relation_name: str,
        expected_relation_interface: str,
        actual_relation_interface: str,
    ):
        self.relation_name = relation_name
        self.expected_relation_interface = expected_relation_interface
        self.actual_relation_interface = actual_relation_interface
        self.message = (
            f"The '{relation_name}' relation has '{actual_relation_interface}' as "
            f"interface rather than the expected '{expected_relation_interface}'"
        )

        super().__init__(self.message)


class RelationRoleMismatchError(Exception):
    """Raised if the relation with the given name has a different direction."""

    def __init__(
        self,
        relation_name: str,
        expected_relation_role: RelationRole,
        actual_relation_role: RelationRole,
    ):
        self.relation_name = relation_name
        self.expected_relation_interface = expected_relation_role
        self.actual_relation_role = actual_relation_role
        self.message = (
            f"The '{relation_name}' relation has role '{repr(actual_relation_role)}' "
            f"rather than the expected '{repr(expected_relation_role)}'"
        )

        super().__init__(self.message)


def _validate_relation_by_interface_and_direction(
    charm: CharmBase,
    relation_name: str,
    expected_relation_interface: str,
    expected_relation_role: RelationRole,
) -> str:
    """Verifies that a relation has the necessary characteristics.

    Verifies that the `relation_name` provided: (1) exists in metadata.yaml,
    (2) declares as interface the interface name passed as `relation_interface`
    and (3) has the right "direction", i.e., it is a relation that `charm`
    provides or requires.

    Args:
        charm: a `CharmBase` object to scan for the matching relation.
        relation_name: the name of the relation to be verified.
        expected_relation_interface: the interface name to be matched by the
            relation named `relation_name`.
        expected_relation_role: whether the `relation_name` must be either
            provided or required by `charm`.

    Raises:
        RelationNotFoundError: If there is no relation in the charm's metadata.yaml
            with the same name as provided via `relation_name` argument.
        RelationInterfaceMismatchError: The relation with the same name as provided
            via `relation_name` argument does not have the same relation interface
            as specified via the `expected_relation_interface` argument.
        RelationRoleMismatchError: If the relation with the same name as provided
            via `relation_name` argument does not have the same role as specified
            via the `expected_relation_role` argument.
    """
    if relation_name not in charm.meta.relations:
        raise RelationNotFoundError(relation_name)

    relation: RelationMeta = charm.meta.relations[relation_name]

    actual_relation_interface = relation.interface_name
    if actual_relation_interface != expected_relation_interface:
        raise RelationInterfaceMismatchError(
            relation_name, expected_relation_interface, actual_relation_interface
        )

    if expected_relation_role == RelationRole.provides:
        if relation_name not in charm.meta.provides:
            raise RelationRoleMismatchError(
                relation_name, RelationRole.provides, RelationRole.requires
            )
    elif expected_relation_role == RelationRole.requires:
        if relation_name not in charm.meta.requires:
            raise RelationRoleMismatchError(
                relation_name, RelationRole.requires, RelationRole.provides
            )
    else:
        raise Exception(f"Unexpected RelationDirection: {expected_relation_role}")


class PrometheusRemoteWriteConsumer(Object):
    """API that manages a required `prometheus_remote_write` relation.

    The `PrometheusRemoteWriteConsumer` is intended to be used by charms that need to push data to
    other charms over the Prometheus remote_write API.

    The `PrometheusRemoteWriteConsumer` object can be instantiated as follows in your charm:

    ```
    from charms.prometheus_k8s.v0.prometheus_remote_write import PrometheusRemoteWriteConsumer

    def __init__(self, *args):
        ...
        self.remote_write_consumer = PrometheusRemoteWriteConsumer(self)
        ...
    ```

    The `PrometheusRemoteWriteConsumer` assumes that, in the `metadata.yaml` of your charm,
    you declare a required relation as follows:

    ```
    requires:
        prometheus-remote-write:  # Relation name
            interface: prometheus_remote_write  # Relation interface
    ```

    Then, inside the part of logic of the charm that needs to access the endpoint addresses,
    they are retrieved with with:

    ```
    self.remote_write_consumer.endpoints
    ```

    Alternatively, the consumer can also provide you with a dictionary structured like the
    Prometheus configuration object (see
    https://prometheus.io/docs/prometheus/latest/configuration/configuration/#remote_write),
    with:

    ```
    self.remote_write_consumer.configs
    ```

    About the name of the relation managed by this library: technically, you *could* change
    the relation name, `prometheus-remote-write`, but that requires you to provide the new
    relation name to the `PrometheusRemoteWriteConsumer` via the `relation_name` constructor
    argument. (The relation interface, on the other hand, is immutable and, if you were to change
    it, your charm would not be able to relate with other charms using the right relation
    interface. The library prevents you from doing that by raising an exception.) In any case, it
    is strongly discouraged to change the relation name: having consistent relation names across
    charms that do similar things is a very good thing for the people that will use your charm.
    The one exception to the rule above, is if you charm needs to both consume and provide a
    relation using the `prometheus_remote_write` interface, in which case changing the relation
    name to differentiate between "incoming" and "outgoing" remote write interactions is necessary.
    """

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        """API to manage a required relation with the `prometheus_remote_write` interface.

        Args:
            charm: The charm object that instantiated this class.
            relation_name: Name of the relation with the `prometheus_remote_write` interface as
                defined in metadata.yaml.

        Raises:
            RelationNotFoundError: If there is no relation in the charm's metadata.yaml
                with the same name as provided via `relation_name` argument.
            RelationInterfaceMismatchError: The relation with the same name as provided
                via `relation_name` argument does not have the `prometheus_scrape` relation
                interface.
            RelationRoleMismatchError: If the relation with the same name as provided
                via `relation_name` argument does not have the `RelationRole.requires`
                role.
        """
        _validate_relation_by_interface_and_direction(
            charm, relation_name, RELATION_INTERFACE_NAME, RelationRole.requires
        )

        super().__init__(charm, relation_name)
        self._relation_name = relation_name

    @property
    def endpoints(self) -> List[str]:
        """A list of remote write endpoints.

        Returns:
            A list of remote write endpoints.
        """
        endpoints = []
        for relation in self.model.relations[self._relation_name]:
            for unit in relation.units:
                if not (endpoint := relation.data[unit].get("remote_write_endpoint")):
                    continue
                endpoints.append(endpoint)
        return endpoints

    @property
    def configs(self) -> list:
        """A config object ready to be dropped in to a prometheus config file.

        Returns:
            A list of remote_write configs.
        """
        return [{"url": endpoint} for endpoint in self.endpoints]


class PrometheusRemoteWriteProvider(Object):
    """API that manages a provided `prometheus_remote_write` relation.

    The `PrometheusRemoteWriteProvider` is intended to be used by charms that need to receive data
    from other charms over the Prometheus remote_write API.

    The `PrometheusRemoteWriteProvider` object can be instantiated as follows in your charm:

    ```
    from charms.prometheus_k8s.v0.prometheus_remote_write import PrometheusRemoteWriteProvider

    def __init__(self, *args):
        ...
        self.remote_write_provider = PrometheusRemoteWriteProvider(self)
        ...
    ```

    The `PrometheusRemoteWriteProvider` assumes that, in the `metadata.yaml` of your charm,
    you declare a provided relation as follows:

    ```
    provides:
        prometheus-remote-write:  # Relation name
            interface: prometheus_remote_write  # Relation interface
    ```

    Remote-write endpoints exposed by your charm are passed to the library as follows:

    ```
    self.remote_write_provider.set_endpoint(
        address=
        port=9090
    )
    ```

    About the name of the relation managed by this library: technically, you *could* change
    the relation name, `prometheus-remote-write`, but that requires you to provide the new
    relation name to the `PrometheusRemoteWriteProducer` via the `relation_name` constructor
    argument. (The relation interface, on the other hand, is immutable and, if you were to change
    it, your charm would not be able to relate with other charms using the right relation
    interface. The library prevents you from doing that by raising an exception.) In any case, it
    is strongly discouraged to change the relation name: having consistent relation names across
    charms that do similar things is a very good thing for the people that will use your charm.
    The one exception to the rule above, is if you charm needs to both consume and provide a
    relation using the `prometheus_remote_write` interface, in which case changing the relation
    name to differentiate between "incoming" and "outgoing" remote write interactions is necessary.
    """

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        """API to manage a provided relation with the `prometheus_remote_write` interface.

        Args:
            charm: The charm object that instantiated this class.
            relation_name: Name of the relation with the `prometheus_remote_write` interface as
                defined in metadata.yaml.

        Raises:
            RelationNotFoundError: If there is no relation in the charm's metadata.yaml
                with the same name as provided via `relation_name` argument.
            RelationInterfaceMismatchError: The relation with the same name as provided
                via `relation_name` argument does not have the `prometheus_scrape` relation
                interface.
            RelationRoleMismatchError: If the relation with the same name as provided
                via `relation_name` argument does not have the `RelationRole.requires`
                role.
        """
        _validate_relation_by_interface_and_direction(
            charm, relation_name, RELATION_INTERFACE_NAME, RelationRole.provides
        )

        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

    def set_endpoint(
        self,
        schema: str = "http",
        address: Optional[str] = None,
        port: Union[str, int] = 9090,
        path: str = "/api/v1/write",
    ) -> None:
        """Set the address and port on which you will serve prometheus remote write.

        If `address` is not provided, the IP of the charm's pod will be used to build
        the endpoint URL that is advertised to the consumer.

        Args:
            schema: The URL schema used to build the remote_write endpoint, e.g., "http" or
                "https"
            address: The address used to build the remote_write endpoint
            port: The port number used to build the remote_write endpoint
            path: The URL path used to build the remote_write endpoint
        """
        if not address:
            network_binding = self._charm.model.get_binding(self._relation_name)
            address = network_binding.network.bind_address

        if path:
            if not path.startswith("/"):
                path = f"/{path}"
        else:
            path = ""

        endpoint_url = f"{schema}://{address}:{str(port)}{path}"

        for relation in self.model.relations[self._relation_name]:
            relation.data[self._charm.unit]["remote_write_endpoint"] = endpoint_url
