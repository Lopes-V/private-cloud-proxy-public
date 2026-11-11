const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const test = require('node:test')
const { TabManager } = require('../browser/tab-manager')
const { BrowserStore } = require('../browser/browser-store')

function setup() {
  const views = []
  class View {
    constructor() {
      this.webContents = new EventEmitter()
      Object.assign(this.webContents, {
        navigationHistory: { canGoBack: () => false, canGoForward: () => false },
        setWindowOpenHandler: handler => { this.open = handler },
        loadURL: url => { this.loaded = url; return Promise.resolve() },
        close: () => { this.closed = true }, stop() {}, focus() {}
      })
      views.push(this)
    }
    setBounds(bounds) { this.bounds = bounds }
    setBackgroundColor(color) { this.background = color }
  }
  const window = {
    view: null,
    children: [],
    getContentSize: () => [1280, 720]
  }
  window.contentView = {
    addChildView(view) { window.children.push(view); window.view = view },
    removeChildView(view) {
      window.children = window.children.filter(child => child !== view)
      if (window.view === view) window.view = null
    }
  }
  const manager = new TabManager({ WebContentsView: View, window, store: new BrowserStore(new Map()), notify() {} })
  return { manager, window, views }
}

test('nova aba e local e nao inicia requisicoes externas', () => {
  const { manager, views, window } = setup()
  manager.createTab()
  assert.equal(manager.snapshot().tabs[0].url, 'browser://newtab')
  assert.equal(views[0].loaded, undefined)
  assert.equal(window.view, null)
})

test('fechar aba inativa preserva a aba selecionada', () => {
  const { manager } = setup()
  const first = manager.createTab('https://example.com')
  manager.createTab('https://example.org')
  const active = manager.createTab('https://example.net')
  manager.closeTab(first)
  assert.equal(manager.activeId, active)
})

test('fechar ultima aba deixa uma nova aba local utilizavel', () => {
  const { manager, views } = setup()
  manager.closeTab(manager.createTab('https://example.com'))
  assert.equal(manager.snapshot().tabs.length, 1)
  assert.equal(manager.snapshot().tabs[0].url, 'browser://newtab')
  assert.equal(views[0].closed, true)
})

test('reabrir recupera o endereco da aba fechada', () => {
  const { manager } = setup()
  manager.closeTab(manager.createTab('https://example.com'))
  manager.command('reopen')
  const state = manager.snapshot()
  assert.equal(state.tabs.find(tab => tab.id === state.activeId).url, 'https://example.com/')
})

test('URL rejeitada nao cria aba nem muda a selecao', () => {
  const { manager } = setup()
  const active = manager.createTab()
  assert.throws(() => manager.createTab('javascript:alert(1)'), /HTTP/)
  assert.equal(manager.tabs.length, 1)
  assert.equal(manager.activeId, active)
})

test('painel lateral reserva espaco sem cobrir a pagina', () => {
  const { manager, views } = setup()
  manager.createTab('https://example.com')
  manager.setPanel('history')
  assert.equal(views[0].bounds.width, 940)
  assert.equal(views[0].bounds.y, 128)
  manager.setPanel(null)
  assert.equal(views[0].bounds.width, 1280)
})

test('falha principal aparece no estado e cancelamento nao vira erro', async () => {
  const { manager, views } = setup()
  manager.createTab('https://example.com')
  const wc = views[0].webContents
  wc.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', 'https://example.com/', true)
  assert.equal(manager.snapshot().tabs[0].error.code, -105)
  manager.navigate('https://example.org')
  wc.emit('did-fail-load', {}, -3, 'ERR_ABORTED', 'https://example.com/', true)
  assert.equal(manager.snapshot().tabs[0].error, null)
})

test('rejeicao de loadURL e capturada sem rejeicao nao tratada', async () => {
  const { manager, views } = setup()
  manager.createTab()
  views[0].webContents.loadURL = () => Promise.reject(new Error('net::ERR_CONNECTION_REFUSED'))
  manager.navigate('https://example.com')
  await new Promise(resolve => setImmediate(resolve))
  assert.match(manager.snapshot().tabs[0].error.message, /ERR_CONNECTION_REFUSED/)
})

test('voltar e avancar indisponiveis nao lancam erro', () => {
  const { manager, views } = setup()
  manager.createTab('https://example.com')
  views[0].webContents.navigationHistory.goBack = () => { throw new Error('sem historico') }
  views[0].webContents.navigationHistory.goForward = () => { throw new Error('sem historico') }
  assert.doesNotThrow(() => { manager.command('back'); manager.command('forward') })
})

test('falha de link na pagina atual mostra o endereco que falhou', () => {
  const { manager, views, window } = setup()
  manager.createTab('https://example.com')
  const wc = views[0].webContents
  wc.emit('did-start-navigation', { url: 'https://example.org/', isSameDocument: false, isMainFrame: true })
  wc.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', 'https://example.org/', true)
  const tab = manager.snapshot().tabs[0]
  assert.equal(tab.url, 'https://example.org/')
  assert.equal(tab.error?.code, -105)
  assert.equal(window.view, null)
})

test('navegacao bem sucedida apos erro volta a exibir a pagina', () => {
  const { manager, views, window } = setup()
  manager.createTab('https://example.com')
  const wc = views[0].webContents
  wc.emit('did-fail-load', {}, -105, 'ERR_NAME_NOT_RESOLVED', 'https://example.com/', true)
  assert.equal(window.view, null)
  wc.emit('did-navigate', {}, 'https://example.org/')
  assert.equal(manager.snapshot().tabs[0].error, null)
  assert.equal(window.view, views[0])
})

test('rejeicao atrasada nao sobrescreve uma navegacao mais recente', async () => {
  const { manager, views } = setup()
  manager.createTab()
  let rejectFirst
  views[0].webContents.loadURL = () => new Promise((_resolve, reject) => { rejectFirst = reject })
  manager.navigate('https://example.com')
  views[0].webContents.loadURL = () => Promise.resolve()
  manager.navigate('https://example.org')
  rejectFirst(new Error('Falha antiga'))
  await new Promise(resolve => setImmediate(resolve))
  const tab = manager.snapshot().tabs[0]
  assert.equal(tab.url, 'https://example.org/')
  assert.equal(tab.error, null)
})

test('navegacao no mesmo documento nao deixa carregamento permanente', () => {
  const { manager, views } = setup()
  manager.createTab('https://example.com')
  const wc = views[0].webContents
  wc.emit('did-stop-loading')
  wc.emit('did-start-navigation', { url: 'https://example.com/#secao', isSameDocument: true, isMainFrame: true })
  wc.emit('did-navigate-in-page', {}, 'https://example.com/#secao', true)
  const tab = manager.snapshot().tabs[0]
  assert.equal(tab.url, 'https://example.com/#secao')
  assert.equal(tab.loading, false)
})

test('troca de aba deixa apenas a aba ativa anexada a janela', () => {
  const { manager, views, window } = setup()
  const first = manager.createTab('https://example.com')
  manager.createTab('https://example.org')
  assert.deepEqual(window.children, [views[1]])
  manager.activateTab(first)
  assert.deepEqual(window.children, [views[0]])
  manager.navigate('browser://newtab')
  assert.deepEqual(window.children, [])
  assert.equal(views[0].background, '#f4f6fb')
})

test('voltar usa a API antiga do webContents no runtime Electron 31', () => {
  const { manager, views } = setup()
  manager.createTab('https://example.com')
  const wc = views[0].webContents
  delete wc.navigationHistory
  let back = 0
  Object.assign(wc, { canGoBack: () => true, canGoForward: () => false, goBack: () => { back++ } })
  assert.equal(manager.snapshot().tabs[0].canGoBack, true)
  manager.command('back')
  assert.equal(back, 1)
})
