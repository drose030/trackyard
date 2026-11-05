const { contextBridge, ipcRenderer } = require("electron");

// Native file dialogs for the editor (it falls back to its own pickers in a browser), and
// a restart once GPU support is downloaded.
contextBridge.exposeInMainWorld("trackyardDesktop", {
  pick: (kind, start) => ipcRenderer.invoke("pick", kind, start),
  restart: () => ipcRenderer.invoke("restart"),
});
