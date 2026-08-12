from unittest.mock import Mock

from provisioner import get_console_ticket


def test_get_console_ticket_calls_vncproxy():
    client = Mock()

    result = get_console_ticket(client, "pve", 1001)

    client.nodes.assert_called_once_with("pve")
    client.nodes.return_value.qemu.assert_called_once_with(1001)
    client.nodes.return_value.qemu.return_value.vncproxy.post.assert_called_once_with(websocket=1)
    assert result == client.nodes.return_value.qemu.return_value.vncproxy.post.return_value
