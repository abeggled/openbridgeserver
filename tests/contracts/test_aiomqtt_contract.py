"""Contract tests for aiomqtt — verifies the API surface used by obs.core.mqtt_client
and obs.adapters.mqtt.

These tests do NOT connect to a broker. They verify that the library's public interface
matches what OBS expects so that a version upgrade that renames parameters or restructures
classes is caught immediately.
"""

from __future__ import annotations

import inspect

import pytest

aiomqtt = pytest.importorskip("aiomqtt", reason="aiomqtt not installed")


class TestClientClass:
    def test_client_is_importable(self):
        assert hasattr(aiomqtt, "Client")

    def test_client_is_async_context_manager(self):
        assert hasattr(aiomqtt.Client, "__aenter__"), "aiomqtt.Client must be an async context manager"
        assert hasattr(aiomqtt.Client, "__aexit__")

    def test_client_constructor_accepts_hostname(self):
        sig = inspect.signature(aiomqtt.Client.__init__)
        assert "hostname" in sig.parameters, (
            "aiomqtt.Client no longer accepts 'hostname'. obs/core/mqtt_client.py uses 'hostname=...' — update if renamed."
        )

    def test_client_constructor_accepts_port(self):
        sig = inspect.signature(aiomqtt.Client.__init__)
        assert "port" in sig.parameters

    def test_client_constructor_accepts_username(self):
        sig = inspect.signature(aiomqtt.Client.__init__)
        assert "username" in sig.parameters

    def test_client_constructor_accepts_password(self):
        sig = inspect.signature(aiomqtt.Client.__init__)
        assert "password" in sig.parameters

    def test_client_constructor_accepts_identifier(self):
        sig = inspect.signature(aiomqtt.Client.__init__)
        assert "identifier" in sig.parameters, (
            "aiomqtt.Client no longer accepts 'identifier'. "
            "obs/adapters/mqtt/adapter.py and obs/core/mqtt_client.py pass 'identifier=...' "
            "to avoid zero-length client IDs being rejected by strict brokers."
        )

    def test_client_constructor_accepts_tls_context(self):
        sig = inspect.signature(aiomqtt.Client.__init__)
        assert "tls_context" in sig.parameters, (
            "aiomqtt.Client no longer accepts 'tls_context'. obs/adapters/mqtt/adapter.py passes 'tls_context=...' for TLS-enabled brokers."
        )


class TestClientMethods:
    def test_client_has_publish(self):
        assert hasattr(aiomqtt.Client, "publish")

    def test_publish_accepts_topic_payload_retain(self):
        sig = inspect.signature(aiomqtt.Client.publish)
        params = list(sig.parameters)
        assert "topic" in params, "aiomqtt.Client.publish missing 'topic' parameter"
        assert "payload" in params, "aiomqtt.Client.publish missing 'payload' parameter"
        assert "retain" in params, "aiomqtt.Client.publish missing 'retain' parameter"

    def test_client_has_subscribe(self):
        assert hasattr(aiomqtt.Client, "subscribe")

    def test_client_has_messages(self):
        # obs/core/mqtt_client.py: async for message in client.messages
        assert hasattr(aiomqtt.Client, "messages"), (
            "aiomqtt.Client no longer has a 'messages' attribute. "
            "The subscriber loop in obs/core/mqtt_client.py uses 'async for message in client.messages'."
        )


class TestMessageClass:
    def test_message_class_exists(self):
        assert hasattr(aiomqtt, "Message"), "aiomqtt.Message class not found"

    def _message_fields(self) -> set[str]:
        msg_cls = aiomqtt.Message
        # Python 3.13+: __static_attributes__ lists slot-like attributes
        static = set(getattr(msg_cls, "__static_attributes__", ()))
        # Fallback: check __init__ parameter names
        init_params = set(inspect.signature(msg_cls.__init__).parameters) - {"self"}
        return static | init_params

    def test_message_has_topic(self):
        assert "topic" in self._message_fields(), (
            "aiomqtt.Message no longer has a 'topic' attribute. mqtt_client.py uses str(message.topic) to extract the topic string."
        )

    def test_message_has_payload(self):
        assert "payload" in self._message_fields(), (
            "aiomqtt.Message no longer has a 'payload' attribute. mqtt_client.py passes message.payload to _handle_set_message()."
        )
