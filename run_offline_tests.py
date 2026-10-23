"""Offline regression tests: no website/provider calls."""
import socket
import sys
import unittest


def block_network(*_args, **_kwargs):
    raise RuntimeError("Public CI test: real network access is disabled")


# Apply before unittest loads the scanner modules.
socket.socket.connect = block_network
socket.socket.connect_ex = block_network
socket.create_connection = block_network
socket.getaddrinfo = block_network

suite = unittest.defaultTestLoader.loadTestsFromNames(
    ["test_hunter", "test_marktplaats", "test_marktplaats_potential"]
)
outcome = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if outcome.wasSuccessful() else 1)
