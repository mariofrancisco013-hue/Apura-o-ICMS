-- Preenche produto_codigo/produto_descricao dos itens JÁ importados antes dessa coluna existir
-- (06/08/2026) — SEM precisar rodar nada fora do SQL Editor do Supabase.
--
-- Por que em lotes: um UPDATE só, cobrindo a tabela inteira (dezenas de milhares de linhas), estourou o
-- timeout do painel do Supabase ("upstream timeout" — limite do proxy do SQL Editor, não do Postgres).
-- Rodando em lotes pequenos, cada clique em "Run" termina rápido e não bate no limite.
--
-- COMO USAR:
-- 1. Cole e rode a QUERY 1 abaixo. Clique em "Run" de novo (sem mudar nada) várias vezes seguidas — cada
--    vez atualiza até 2.000 linhas. Quando o resultado mostrar "UPDATE 0", essa parte terminou.
-- 2. Cole e rode a QUERY 2 (cobre o caso raro de "produto" sem separador " - "). Mesma lógica: rode várias
--    vezes até dar "UPDATE 0".
-- 3. (Opcional) Rode a QUERY 3 para conferir quantas linhas ainda faltam, se quiser acompanhar o progresso
--    sem ficar só olhando o "UPDATE N".
--
-- Itens importados A PARTIR de agora já vêm com produto_codigo/produto_descricao preenchidos direto pela
-- importação (app/lib/importacao.py) — isso aqui é só para arrumar o histórico.

-- QUERY 1 — repita até "UPDATE 0"
update notas_fiscais_itens
set produto_codigo = trim(split_part(produto, ' - ', 1)),
    produto_descricao = trim(substring(produto from strpos(produto, ' - ') + 3))
where id in (
    select id from notas_fiscais_itens
    where produto_codigo is null and produto ~ ' - '
    limit 2000
);

-- QUERY 2 — repita até "UPDATE 0" (cobre "produto" sem " - ", célula não segue o padrão esperado)
update notas_fiscais_itens
set produto_descricao = trim(produto)
where id in (
    select id from notas_fiscais_itens
    where produto_codigo is null and produto_descricao is null and produto is not null
    limit 2000
);

-- QUERY 3 — conferir quanto falta (opcional)
select count(*) as itens_faltando
from notas_fiscais_itens
where produto_codigo is null and produto is not null;
