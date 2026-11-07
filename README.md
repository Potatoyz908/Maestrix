<p align="center"><img src="Sources/Assets.xcassets/AppIcon.appiconset/icon_512x512@2x.png" alt="Ícone Maestrix" width="96"></p>

# Maestrix

Software Linux desenvolvido por **Euller**, com interface nativa GTK 4 e canvas para organizar agentes de IA, terminais, notas, arquivos e navegadores.

O Maestrix é um projeto derivado do [OpenMaestri para macOS](https://github.com/zlh-428/open-maestri). O [Maestri original](https://maestriapp.com) é a referência de conceito, interface e funcionamento. Este projeto mantém os créditos dos autores originais e a licença GPL-3.0-or-later.

## Interface e demonstração

As imagens e o GIF abaixo são os arquivos já presentes no repositório. Podem mostrar a identidade anterior à mudança para Maestrix.

![Demonstração rápida](docs/images/demo.gif)
![Visão geral](docs/images/home.png)
![Agentes no canvas](docs/images/agents.png)
![Criação de terminal](docs/images/create-terminal.png)
![Arquivos](docs/images/file.png)

## Instalação no Linux

Consulte [o guia Linux](README.linux.md) para dependências e execução. Com as dependências instaladas, na raiz do repositório:

```bash
swift build
./scripts/install-desktop.sh
maestrix
```

O menu de aplicativos passa a mostrar **Maestrix**, com o ícone deste repositório. O comando `open-maestri` continua disponível como alias de compatibilidade; `maestrix-cli` é o CLI dos agentes, com `omaestri` mantido como alias. O backend é exposto como `maestrix-server` e o lançador como `maestrix.desktop`.

## Compatibilidade

Os workspaces existentes em `~/.open-maestri`, o protocolo `maestri://`, os identificadores internos e as variáveis `OPEN_MAESTRI_*` são mantidos para preservar dados e integrações. A mudança de nome não exige migrar seus projetos.

## Créditos e licença

- **Euller**: desenvolvimento e adaptação do Maestrix para Linux.
- **OpenMaestri e seus contribuidores**: base de código para macOS e recursos herdados, incluindo o ícone presente no repositório.
- **Maestri original**: referência de produto, conceito e interface.
- Bibliotecas e dependências mantêm suas licenças e atribuições próprias.

Veja [LICENSE](LICENSE), [NOTICE](NOTICE) e a [documentação herdada](docs/UPSTREAM-README.md). Maestrix é um derivado independente; não é uma versão oficial dos projetos de origem.
