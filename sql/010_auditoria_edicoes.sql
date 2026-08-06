-- Histórico de ajustes manuais feitos na Planilha de Entrada/Saída — pedido do usuário em 06/08/2026:
-- "não localizei onde eu vejo os ajustes que foram feitos". Até aqui, editar um item na grade (ex:
-- corrigir um CFOP errado) sobrescrevia o valor direto na notas_fiscais_itens sem deixar rastro de qual
-- era o valor antes, quem mudou e quando — importante numa apuração de imposto, onde pode ser cobrado
-- depois "por que esse CFOP está diferente do relatório original do Winthor".

create table if not exists auditoria_edicoes_planilha (
    id               bigserial primary key,
    nf_item_id       bigint not null references notas_fiscais_itens(id) on delete cascade,
    competencia_id   bigint not null references competencias(id) on delete cascade,
    tipo_operacao    text not null check (tipo_operacao in ('entrada', 'saida')),
    campo            text not null,
    valor_anterior   text,
    valor_novo       text,
    editado_por      uuid references auth.users(id),
    editado_por_email text,
    editado_em       timestamptz not null default now()
);
create index if not exists ix_auditoria_edicoes_competencia
    on auditoria_edicoes_planilha(competencia_id, tipo_operacao, editado_em desc);
create index if not exists ix_auditoria_edicoes_item on auditoria_edicoes_planilha(nf_item_id);
comment on table auditoria_edicoes_planilha is
    'Um registro por CAMPO alterado manualmente na grade da Planilha de Entrada/Saída (não por linha) — '
    'ex: editar CFOP e valor_icms de um item numa mesma gravação gera 2 linhas aqui. Guarda o valor de '
    'antes e depois (sempre como texto, pra caber qualquer tipo de coluna), quem editou e quando. Só serve '
    'pra consulta/auditoria — não é usado por nenhum cálculo.';

alter table auditoria_edicoes_planilha enable row level security;
drop policy if exists "authenticated_full_access" on auditoria_edicoes_planilha;
create policy "authenticated_full_access" on auditoria_edicoes_planilha
    for all to authenticated using (true) with check (true);
