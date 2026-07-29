"""Test doubles and the contract that keeps them honest.

Importing `contract` pulls in pytest, so it is not re-exported here — a
production import of `cosmic.testing.fakes` should not require a test runner.
Import `RepositoryContractTests` from `cosmic.testing.contract` directly.
"""
from cosmic.testing.fakes import FakeRepo, InMemoryUnitOfWork

__all__ = ["FakeRepo", "InMemoryUnitOfWork"]
