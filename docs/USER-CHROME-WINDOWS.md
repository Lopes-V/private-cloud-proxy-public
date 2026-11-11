# ChromeProtegido fornecido + Private Cloud Proxy

O pacote `PrivateCloudProxy-UserChrome-Windows.zip` inclui o runtime Windows
original fornecido pelo usuário, os fontes do navegador adaptados dentro de
`resources/app.asar`, a bridge Node e suas dependências fixadas.

Extraia o ZIP inteiro em uma pasta gravável. Copie `.env.example` para `.env`
e preencha `TUNNEL_TOKEN` se quiser mantê-lo local; `.env` é ignorado pelo Git
e não acompanha o pacote. Deixe-o vazio para digitar o segredo ao iniciar.
Execute `Start-Private-Proxy.cmd`.
O launcher seleciona automaticamente `ChromeProtegido/ChromeProtegido.exe` e o
endpoint público `wss://seu-servico.onrender.com/tunnel`. Na primeira
execução, prepara Node 24.22.1 pelo download oficial com SHA256 fixado. Não exige
instalar outro navegador nem privilégios de administrador. O fluxo existente da
bridge carrega `TUNNEL_TOKEN` do `.env` ou do ambiente. Sem esse valor, pede a
credencial com entrada oculta. Ela permanece na bridge e não é repassada ao
processo Electron.

O navegador recebe somente a porta do proxy local. Sem a bridge, a navegação
falha. A página inicial local continua disponível. As configurações de rota são
gerenciadas pelo Private Cloud Proxy.

## Construção reproduzível a partir do arquivo fornecido

Requer Node e Python 3.11 ou posterior. Na raiz do repositório:

```text
npm ci --omit=dev --ignore-scripts --no-audit --no-fund
npm ci --prefix tools/user-chrome --ignore-scripts --no-audit --no-fund
python -m unittest discover -s scripts/test -p test_package_user_chrome.py
python scripts/package-user-chrome.py --archive CAMINHO/main.zip
```

`--archive` é obrigatório: o `main.zip` do runtime não é baixado pelo script nem
distribuído neste repositório. O arquivo deve ter exatamente 157.426.933 bytes e
SHA256 `b0ec400dbe6dd60c5b17c1e1d178b933f43966fb06a0cb70dea42cf541db1676`.
O tamanho e o SHA-256 são verificados antes de qualquer uso.

O empacotador usa `@electron/asar` 3.4.1 fixado no lockfile. Ele verifica nomes e
limites antes de extrair o runtime, preserva todos os arquivos originais exceto
`resources/app.asar`, compara os hashes dos demais arquivos e confere a extração
do ASAR produzido. As licenças originais acompanham o runtime. O arquivo
`User-Chrome-Provenance.json` registra a origem, os hashes do runtime original e
dos fontes incluídos; em CI, também registra o commit exato em `sourceCommit`. O ZIP final e seu SHA256 ficam em `dist/`; não devem entrar
no Git.

## Verificação nativa e CI

Em Windows, após construir o ZIP:

```text
python scripts/smoke-user-chrome.py --bundle dist/PrivateCloudProxy-UserChrome-Windows.zip
```

O teste copia o runtime para uma pasta temporária, confere seus hashes, cria um
ASAR de teste com os mesmos fontes e um ponto de entrada de integração, e executa
o EXE original. Usa perfil isolado e remove `TUNNEL_TOKEN` do ambiente do
processo. A navegação parte da API preload real e é verificada em uma WebContentsView
real: a resposta deve vir de um proxy HTTP de teste; uma origem em localhost
permanece inacessada. Em seguida, navega para `https://example.com/` por um túnel
CONNECT que aceita somente `example.com:443`, encaminha os bytes TLS reais e
exige o título `Example Domain`. Chromium valida o certificado real do site com
sua confiança normal. Depois de parar o proxy e destruir ambos os lados dos
túneis, a navegação deve exibir erro sem acessar diretamente a origem local.
O relatório aprovado identifica a versão de `electron` fixada em
`user-chrome/package.json` (hoje 45.2.3), a autoridade CONNECT observada
e o transporte `HTTP+HTTPS CONNECT`.

Esse teste depende do acesso público a example.com no runner Windows e precisa
passar antes de publicar o pacote. Não usa certificados de teste nem opções
para ignorar TLS. O comportamento de proteção de captura depende do Windows e
não é validado por esse teste. O runtime é fornecido pelo usuário e o pacote não
recebe uma nova assinatura digital.

O workflow `user-chrome.yml` (manual, `workflow_dispatch`) usa `windows-latest` e executa a suite do navegador e do
empacotador, além de verificar sintaxe e imports da bridge. Ele **não** empacota nem publica: o runtime
(`main.zip`) não fica no repositório. O pacote é gerado localmente com `--archive`. A suite completa do proxy
continua no workflow CI Linux, pois suas fixtures de curl e certificados usam caminhos Linux.

## Finalidade e credenciais

Projeto experimental para estudo. O autor não se responsabiliza pela forma de uso por terceiros nem por consequências de uso indevido, nos limites da legislação aplicável. Cada usuário deve respeitar leis, políticas de rede e autorizações. Não há garantia de disponibilidade ou adequação para produção.

O pacote não inclui chaves privadas nem o segredo real do túnel. A autenticação permanece obrigatória: informe seu próprio TUNNEL_TOKEN no iniciador, com entrada oculta. O filtro do processo do navegador remove o segredo independentemente de maiúsculas/minúsculas no nome da variável do Windows.
