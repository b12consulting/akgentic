"""Tests for public API namespace structure (Story 1.10a)."""


class TestAkgenticNamespace:
    """Verify akgentic top-level exports agent primitives only."""

    def test_agent_primitives_importable(self) -> None:
        from akgentic.core import (
            ActorAddress,
            ActorAddressImpl,
            ActorAddressProxy,
            ActorAddressStopped,
            ActorSystem,
            AgentConfig,
            Akgent,
            AkgentDeserializeContext,
            AkgentStateObserver,
            BaseConfig,
            BaseState,
            ExecutionContext,
            Statistics,
        )

        assert Akgent is not None
        assert AkgentDeserializeContext is not None
        assert ActorSystem is not None
        assert ExecutionContext is not None
        assert Statistics is not None
        assert ActorAddress is not None
        assert ActorAddressImpl is not None
        assert ActorAddressProxy is not None
        assert ActorAddressStopped is not None
        assert AgentConfig is not None
        assert BaseConfig is not None
        assert AkgentStateObserver is not None
        assert BaseState is not None

    def test_version_accessible(self) -> None:
        from akgentic.core import __version__

        assert __version__ == "1.0.0-alpha.2"

    def test_message_types_not_in_all(self) -> None:
        import akgentic.core

        message_types = {
            "Message",
            "UserMessage",
            "ResultMessage",
            "StopRecursively",
            "StartMessage",
            "StopMessage",
            "SentMessage",
            "ReceivedMessage",
            "ProcessedMessage",
            "ErrorMessage",
            "StateChangedMessage",
            "EventMessage",
            "date_time_factory",
        }
        assert not message_types.intersection(set(akgentic.core.__all__))

    def test_serialization_utils_not_in_all(self) -> None:
        import akgentic.core

        util_symbols = {
            "SerializableBaseModel",
            "serialize",
            "serialize_base_model",
            "serialize_type",
            "get_field_serializers_map",
            "ActorAddressDict",
            "DeserializeContext",
            "deserialize_object",
            "import_class",
            "is_uuid_canonical",
        }
        assert not util_symbols.intersection(set(akgentic.core.__all__))

    def test_no_duplicate_symbols_in_all(self) -> None:
        import akgentic.core

        assert len(akgentic.core.__all__) == len(set(akgentic.core.__all__))


class TestMessagesNamespace:
    """Verify akgentic.messages exports all message types."""

    def test_all_message_types_importable(self) -> None:
        from akgentic.core.messages import (
            ErrorMessage,
            EventMessage,
            Message,
            ProcessedMessage,
            ReceivedMessage,
            ResultMessage,
            SentMessage,
            StartMessage,
            StateChangedMessage,
            StopMessage,
            StopRecursively,
            UserMessage,
            date_time_factory,
        )

        assert Message is not None
        assert StartMessage is not None
        assert StopMessage is not None
        assert StopRecursively is not None
        assert UserMessage is not None
        assert ResultMessage is not None
        assert SentMessage is not None
        assert ReceivedMessage is not None
        assert ProcessedMessage is not None
        assert ErrorMessage is not None
        assert StateChangedMessage is not None
        assert EventMessage is not None
        assert date_time_factory is not None

    def test_messages_all_defined(self) -> None:
        import akgentic.core.messages as msgs

        expected = {
            "Message",
            "UserMessage",
            "ResultMessage",
            "StopRecursively",
            "date_time_factory",
            "ErrorMessage",
            "ProcessedMessage",
            "ReceivedMessage",
            "SentMessage",
            "StartMessage",
            "StateChangedMessage",
            "StopMessage",
            "EventMessage",
        }
        assert expected.issubset(set(msgs.__all__))


class TestUtilsNamespace:
    """Verify akgentic.utils exports serialization infrastructure."""

    def test_all_utils_importable(self) -> None:
        from akgentic.core.utils import (
            ActorAddressDict,
            DeserializeContext,
            SerializableBaseModel,
            deserialize_object,
            get_field_serializers_map,
            import_class,
            is_uuid_canonical,
            serialize,
            serialize_base_model,
            serialize_type,
        )

        assert SerializableBaseModel is not None
        assert ActorAddressDict is not None
        assert DeserializeContext is not None
        assert serialize is not None
        assert serialize_base_model is not None
        assert serialize_type is not None
        assert get_field_serializers_map is not None
        assert deserialize_object is not None
        assert import_class is not None
        assert is_uuid_canonical is not None

    def test_utils_all_defined(self) -> None:
        import akgentic.core.utils as utils

        expected = {
            "SerializableBaseModel",
            "serialize",
            "serialize_base_model",
            "serialize_type",
            "get_field_serializers_map",
            "ActorAddressDict",
            "DeserializeContext",
            "deserialize_object",
            "import_class",
            "is_uuid_canonical",
        }
        assert expected.issubset(set(utils.__all__))


class TestNoSymbolOverlap:
    """Verify no symbol appears in more than one __all__."""

    def test_no_overlap_between_namespaces(self) -> None:
        import akgentic.core
        import akgentic.core.messages
        import akgentic.core.utils

        top_level = set(akgentic.core.__all__)
        messages = set(akgentic.core.messages.__all__)
        utils = set(akgentic.core.utils.__all__)

        assert not top_level.intersection(messages), (
            f"Overlap between akgentic and akgentic.messages: {top_level.intersection(messages)}"
        )
        assert not top_level.intersection(utils), (
            f"Overlap between akgentic and akgentic.utils: {top_level.intersection(utils)}"
        )
        assert not messages.intersection(utils), (
            f"Overlap between akgentic.messages and akgentic.utils: {messages.intersection(utils)}"
        )
