// Electron shell: loads the built dashboard from renderer/ (copied out of
// frontend/dist) or, in development, whatever APP_URL points at.

const path = require('node:path')

const { app, BrowserWindow, shell } = require('electron')

function createWindow() {
  const window = new BrowserWindow({
    width: 1600,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#0b1020',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  // News headlines link out to the open web; those belong in the default
  // browser, not in new Electron windows.
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url)
    return { action: 'deny' }
  })

  const devUrl = process.env.APP_URL
  if (devUrl) {
    window.loadURL(devUrl)
  } else {
    window.loadFile(path.join(__dirname, 'renderer', 'index.html'))
  }
}

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
