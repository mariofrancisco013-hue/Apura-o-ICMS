-- Agrupamento de inconsistências repetidas + vínculo com os itens de NF, para sinalizar direto na
-- Planilha de Entrada/Saída (pedido do usuário em 06/08/2026: "as inconsistencias encontradas não estão
-- sendo apresentadas na planilha de entrada e saida" + "um mesmo erro pode se repetir, é melhor que ele
-- agrupe as inconsistencias").

alter table inconsistencias
    add column if not exists chave_agrupamento text,
    add column if not exists quantidade integer not null default 1;

comment on column inconsistencias.chave_agrupamento is
    'Chave usada para agrupar ocorrências do mesmo erro dentro da mesma competência/tipo (ex: o próprio '
    'NCM, ou "parceiro|cfop" para transferência) — cada combinação (competencia_id, tipo, '
    'chave_agrupamento) vira UMA linha em inconsistencias, com quantidade = número de itens de NF por '
    'trás dela (ver inconsistencia_itens para o detalhe item a item).';
comment on column inconsistencias.quantidade is
    'Quantos itens de NF (notas_fiscais_itens) geraram esta mesma inconsistência agrupada.';

create table if not exists inconsistencia_itens (
    id                bigserial primary key,
    inconsistencia_id bigint not null references inconsistencias(id) on delete cascade,
    nf_item_id        bigint not null references notas_fiscais_itens(id) on delete cascade
);
create index if not exists ix_inconsistencia_itens_inc on inconsistencia_itens(inconsistencia_id);
create index if not exists ix_inconsistencia_itens_item on inconsistencia_itens(nf_item_id);
comment on table inconsistencia_itens is
    'Liga cada inconsistência AGRUPADA aos itens de NF específicos por trás dela — usado pela Planilha de '
    'Entrada/Saída para mostrar um sinal de alerta direto na linha do item que tem inconsistência '
    'pendente, sem precisar ir na aba Inconsistências.';

alter table inconsistencia_itens enable row level security;
drop policy if exists "authenticated_full_access" on inconsistencia_itens;
create policy "authenticated_full_access" on inconsistencia_itens
    for all to authenticated using (true) with check (true);

-- Não precisa de backfill manual: as inconsistências antigas (sem agrupamento, uma linha por item) somem
-- e são recriadas já agrupadas na próxima vez que "Calcular apuração" for clicado — a rotina já apaga e
-- recria essas 4 categorias do zero a cada cálculo.
