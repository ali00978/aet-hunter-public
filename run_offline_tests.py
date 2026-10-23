"""Run Marktplaats and Facebook regression tests without external network calls."""
import socket
import sys
import unittest


def block_network(*_args, **_kwargs):
    raise RuntimeError("Offline public development tests cannot access the network")


socket.socket.connect = block_network
socket.socket.connect_ex = block_network
socket.create_connection = block_network
socket.getaddrinfo = block_network

suite = unittest.defaultTestLoader.loadTestsFromNames(
    ["test_hunter", "test_marktplaats", "test_marktplaats_potential",
     "test_facebook_vehicle_filter", "test_facebook_hunter", "test_modal_bridge",
     "test_public_actions", "test_connection_preflight",
     "test_readonly_connection_check",
     "test_aet_pro_handshake", "test_apify_actor_readiness",
     "test_private_snapshot_preflight", "test_live_marktplaats_smoke",
     "test_private_pr_offline_validation", "test_public_hunter_runtime",
     "test_github_standalone", "test_facebook_live_once",
     "test_facebook_recover_existing"]
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
