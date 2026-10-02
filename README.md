# Apuração ICMS — Sodine Atacado F3

Plataforma de apuração de ICMS, reconstruída em 05/08/2026 (Supabase + Streamlit + GitHub). Substitui o
fluxo anterior em planilha. Módulo em desenvolvimento: **ICMS Normal**. Módulos futuros: ICMS Substituição
Tributária, ICMS Antecipado, ICMS Adicional de 10%.

A metodologia completa (regras de negócio, checkpoints de validação, achados sobre os relatórios-fonte)
está documentada no projeto Claude "Apuração ICMS" (`claude/metodologia-icms-normal.md`,
`claude/arquitetura-plataforma.md`, `claude/empresas-grupo.md`) — vale ler antes de mexer no código.

## Arquitetura
- **Banco**: Postgres via Supabase, acessado por conexão direta (SQLAlchemy + psycopg2) — não usa a API
  REST/PostgREST do Supabase. Funciona sem mudança de código com qualquer Postgres gerenciado, bastando
  trocar `DATABASE_URL`.
- **Autenticação**: Supabase Auth (login/senha). É a única parte do app específica do Supabase.
- **Frontend**: Streamlit, publicado no Streamlit Community Cloud.
- **Todos os usuários logados têm o mesmo nível de acesso** (sem perfis admin/analista por enquanto).

## Setup

1. Crie um projeto no Supabase (banco + Auth).
2. Rode as migrações SQL: `sql/001_schema.sql` no SQL Editor do Supabase.
3. Copie `.env.example` → `.env` (para rodar scripts localmente) e `.streamlit/secrets.toml.example` →
   `.streamlit/secrets.toml` (para rodar o app), preenchendo `DATABASE_URL`, `SUPABASE_URL` e
   `SUPABASE_ANON_KEY`.
4. Crie os usuários da equipe no painel do Supabase (Authentication → Users) — 2 a 5 pessoas, mesmo nível
   de acesso para todos.
5. Instale as dependências: `pip install -r requirements.txt`.
6. Carregue os dados de referência:
   ```
   python scripts/seed_cfop.py
   python scripts/seed_empresas.py
   ```
7. Importe um período:
   ```
   python scripts/import_relatorios.py --empresa-cnpj 07.342.785/0005-53 --ano 2026 --mes 7 \
       --entrada RELATORIO_ENTRADA.xls --saida RELATORIO_SAIDA.xls
   ```
   Use `--substituir` para reimportar um período (relatório corrigido) sem duplicar notas.
8. Rode o app: `streamlit run app/Home.py`.

## Validação da metodologia

`scripts/validar_metodologia.py` reproduz a lógica de cálculo em pandas puro (sem precisar de banco) e
compara contra os valores oficiais do livro fiscal (Rotina 1025). Rodado contra julho/2026 (Sodine Atacado
F3) em 05/08/2026, o "13 - Imposto a Recolher" calculado bateu **exato** (R$ 6.921,47, diferença R$ 0,00)
contra o livro oficial, depois de incluir o lançamento manual do CFOP 1602 (R$ 3.814,87 — não vem no
relatório de Entrada, é lançado direto no sistema contábil). Ver comentário no topo do script para detalhes
e a linha de comando usada.

## Pontos em aberto (ver `claude/metodologia-icms-normal.md` no projeto para detalhes)
- CFOPs 1403 e 1411 (Entrada): diferença de ~R$ 283 entre o calculado a partir do relatório de NF e o
  valor do livro oficial — não afeta o resultado final (se cancela no saldo), mas seria bom entender a
  causa para os próximos módulos (ST, Antecipado).
- A validação de "transferência entre empresas não vinculadas" é heurística por nome do parceiro — os
  relatórios de Entrada/Saída não trazem o CNPJ do parceiro. Precisa de um relatório de cadastro de
  parceiros (com CNPJ) para ficar exata.
- A tabela de CFOP tem descrições truncadas no export de origem — 2 códigos (6108, 6202) precisaram de
  ajuste manual para bater com o livro real. Vale revisar a lista completa com o contador antes de confiar
  100% na classificação automática para CFOPs que ainda não apareceram nos dados importados.
