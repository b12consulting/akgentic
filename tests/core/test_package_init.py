"""Test package initialization and structure."""

import importlib.metadata

import akgentic.core


def test_package_version() -> None:
    """Test that package version is correctly set."""
    assert akgentic.core.__version__ == "1.0.0-alpha.2"


def test_package_metadata() -> None:
    """Test that package metadata is accessible."""
    metadata = importlib.metadata.metadata("akgentic")
    assert metadata["Name"] == "akgentic"
    # Python normalizes "alpha.2" to "a2" in package version
    assert metadata["Version"] == "1.0.0a2"


def test_package_has_all_attribute() -> None:
    """Test that __all__ is defined."""
    assert hasattr(akgentic.core, "__all__")
    assert "__version__" in akgentic.core.__all__
