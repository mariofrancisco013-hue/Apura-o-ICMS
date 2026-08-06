-- Separa código e descrição do produto (pedido do usuário em 06/08/2026: "quero a separação entre código
-- e descrição do produto, tanto na entrada quanto na saída").
--
-- Confirmado com o usuário: no relatório Winthor a coluna "Produto" vem numa célula só, no formato
-- "<código> - <descrição>" (ex: "000123 - TESOURA FUTURO"). A tabela `produto` (raw, célula inteira) é
-- mantida como estava, sem quebrar nada que já dependia dela — as duas colunas novas são derivadas dela.
--
-- IMPORTANTE (06/08/2026): esta versão só faz ALTER TABLE + índice + comentários — tudo metadado, roda em
-- menos de 1 segundo. O backfill dos ~47 mil itens já importados (que precisa reescrever cada linha) foi
-- REMOVIDO daqui porque estourava o timeout do SQL Editor do Supabase ("upstream timeout" — é um limite do
-- proxy do painel, não do Postgres em si). Depois de rodar este arquivo, rode
-- `python scripts/backfill_produto_codigo.py` no seu computador (mesmo jeito do seed_ncm.py) para
-- preencher produto_codigo/produto_descricao dos itens antigos — sem limite de tempo do painel.

alter table notas_fiscais_itens
    add column if not exists produto_codigo text,
    add column if not exists produto_descricao text;

create index if not exists ix_nfi_produto_codigo on notas_fiscais_itens(produto_codigo);

comment on column notas_fiscais_itens.produto is
    'Célula original, inteira, do relatório Winthor ("<código> - <descrição>") — mantida como registro '
    'bruto/auditoria. Não é mais usada nas grades da tela; use produto_codigo/produto_descricao.';
comment on column notas_fiscais_itens.produto_codigo is
    'Código do produto (Winthor), extraído automaticamente de "produto" na importação — parte antes do '
    'primeiro " - ". Editável na grade.';
comment on column notas_fiscais_itens.produto_descricao is
    'Descrição do produto, extraída automaticamente de "produto" na importação — parte depois do primeiro '
    '" - ". Editável na grade.';
