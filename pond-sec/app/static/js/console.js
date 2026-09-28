import RFB from "/static/novnc/core/rfb.js";

const screen = document.getElementById("console-screen");
const instanceId = screen.dataset.instanceId;
const ticket = screen.dataset.vncPassword;
const port = screen.dataset.vncPort;

const proto = window.location.protocol === "https:" ? "wss" : "ws";
const url =
    `${proto}://${window.location.host}/themes/session/${instanceId}/console/ws` +
    `?ticket=${encodeURIComponent(ticket)}&port=${encodeURIComponent(port)}`;

const rfb = new RFB(screen, url, {
    credentials: { password: ticket },
});
rfb.scaleViewport = true;
