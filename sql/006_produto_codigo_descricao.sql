-- Separa código e descrição do produto (pedido do usuário em 06/08/2026: "quero a separação entre código
-- e descrição do produto, tanto na entrada quanto na saída").
--
-- Confirmado com o usuário: no relatório Winthor a coluna "Produto" vem numa célula só, no formato
-- "<código> - <descrição>" (ex: "000123 - TESOURA FUTURO"). A tabela `produto` (raw, célula inteira) é
-- mantida como estava, sem quebrar nada que já dependia dela — as duas colunas novas são derivadas dela.

alter table notas_fiscais_itens
    add column if not exists produto_codigo text,
    add column if not exists produto_descricao text;

-- Backfill dos itens já importados: separa pelo primeiro " - ". Quando não há " - " na célula (formato
-- inesperado), joga o texto inteiro em produto_descricao e deixa produto_codigo em branco — mais seguro
-- que adivinhar errado onde termina o código.
update notas_fiscais_itens
set produto_codigo = trim(split_part(produto, ' - ', 1)),
    produto_descricao = trim(substring(produto from strpos(produto, ' - ') + 3))
where produto_codigo is null and produto ~ ' - ';

update notas_fiscais_itens
set produto_descricao = trim(produto)
where produto_codigo is null and produto_descricao is null and produto is not null;

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
