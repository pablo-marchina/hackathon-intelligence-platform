> **Atualização local (Iteração 20):** o ZIP desta entrega inclui migração `0011` e 413 testes locais; esta cópia não foi enviada ao GitHub. Evite publicar por cima de uma branch remota divergente, pois os checkpoints 16–19 podem não ter sido sincronizados.

# Publicação no GitHub — Iteração 15

Este pacote foi preparado em 19/10/2026 como um repositório Git local, **não publicado remotamente**. O repositório novo deve ser **privado**, pois a W6 ainda não foi aprovada para produção.

## Publicação com Windows PowerShell

1. Crie uma pasta local extraindo `hackathon-intelligence-platform-github-ready.zip` (incluindo a pasta oculta `.git`).
2. Instale [Git for Windows](https://git-scm.com/download/win) e [GitHub CLI](https://cli.github.com/) se necessário.
3. Abra o PowerShell nessa pasta e autentique-se com `gh auth login`.
4. Execute `powershell -ExecutionPolicy Bypass -File .\publish-to-github.ps1`.
5. Confirme no navegador que `https://github.com/pablo-marchina/hackathon-intelligence-platform` está marcado como **Private**.

O script falha se o repositório já existir. Ele **não** força push nem substitui um projeto preexistente. Para usar outro nome, execute `powershell -ExecutionPolicy Bypass -File .\publish-to-github.ps1 -Repository nome-alternativo`.

## Estado e cuidados

- Ponto de partida: **W6 Iteração 15**, 351 testes Python aprovados localmente.
- Não são garantidos o build de Next.js, PostgreSQL/Redis/Celery reais, recuperação ou testes E2E em staging.
- Revise `.env.example` e crie seu `.env` localmente; **nunca** faça commit das credenciais reais.
- O commit inicial representa apenas o snapshot recebido, não o histórico Git completo anterior.
- Para aceitar contribuições de terceiros ou mudar a visibilidade para pública, faça uma auditoria de segurança adicional.
