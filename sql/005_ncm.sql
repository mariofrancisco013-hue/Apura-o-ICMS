-- Tabela de referência oficial de NCM (Nomenclatura Comum do Mercosul), baixada pelo usuário no sistema
-- Classif do governo (formato JSON) em 06/08/2026, vigente em 06/08/2026 (Resolução Gecex nº 812/2025).
-- Usada para mostrar a descrição oficial do produto ao lado do código NCM (nas planilhas de Entrada/Saída
-- e na aba "NCMs Tributados"), em vez do analista ter que decorar ou pesquisar o que cada código significa.
--
-- A descrição vem RECONSTRUÍDA a partir da hierarquia do NCM (capítulo > posição > subposição > item) —
-- o JSON de origem só traz o texto do último nível ("Outras", "Outros", etc., sem contexto), então o
-- carregamento (scripts/seed_ncm.py) já monta a cadeia completa antes de gravar em data/ncm.csv. Exemplo
-- real: NCM 82130000 vira "Ferramentas, artigos de cutelaria e talheres, e suas partes, de metais comuns.
-- > Tesouras e suas lâminas." — bate com o exemplo que o próprio usuário deu no início do projeto.
--
-- Rode este arquivo no SQL Editor do Supabase para criar a tabela; os dados (10.515 códigos) são grandes
-- demais para colar em SQL — carregue com `python scripts/seed_ncm.py` no seu computador (ver instruções
-- no próprio script), do mesmo jeito que os outros scripts/seed_*.py.

create table if not exists ncm (
    codigo             text primary key,   -- 8 dígitos, sem pontuação (ex: '82130000')
    descricao          text not null,      -- descrição completa, com a cadeia hierárquica concatenada
    descricao_curta    text,               -- só o último nível (ex: "Outras") — guardado por completude
    updated_at         timestamptz not null default now()
);

alter table ncm enable row level security;
drop policy if exists "authenticated_full_access" on ncm;
create policy "authenticated_full_access" on ncm
    for all to authenticated using (true) with check (true);
