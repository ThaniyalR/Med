const installButton = document.querySelector("#install-app");
const installStatus = document.querySelector("#install-app-status");
let installPrompt;

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch((error) => {
    console.error("Nutrix service worker registration failed.", error);
  });
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  installButton.hidden = false;
});

installButton.addEventListener("click", async () => {
  if (!installPrompt) {
    return;
  }

  installPrompt.prompt();
  const { outcome } = await installPrompt.userChoice;
  installStatus.textContent = outcome === "accepted" ? "Nutrix is installing." : "";
  installPrompt = null;
  installButton.hidden = true;
});

window.addEventListener("appinstalled", () => {
  installButton.hidden = true;
  installStatus.textContent = "Nutrix is installed on this device.";
  installPrompt = null;
});
