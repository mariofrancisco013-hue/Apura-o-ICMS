-- CFOPs que o analista marca como "não precisa validar", por empresa — pedido do usuário em 06/08/2026.
-- Alguns CFOPs geram alerta recorrente nas validações automáticas por um motivo que o analista já conhece
-- e não é erro de verdade (ex: um CFOP usado só num caso muito específico da operação). Em vez de
-- justificar/ignorar a mesma inconsistência todo mês, o CFOP inteiro pode ser excluído das 3 checagens
-- (NCM×ST divergente, transferência não vinculada, NCM tributado como ST/novo) — os itens continuam
-- normalmente na Planilha e na Apuração, só não entram mais nessas validações.

create table if not exists cfops_sem_validacao (
    id                bigserial primary key,
    empresa_id        bigint not null references empresas(id) on delete cascade,
    cfop              integer not null references cfop(codigo),
    motivo            text,
    criado_por        uuid references auth.users(id),
    criado_por_email  text,
    created_at        timestamptz not null default now(),
    unique (empresa_id, cfop)
);
create index if not exists ix_cfops_sem_validacao_empresa on cfops_sem_validacao(empresa_id);
comment on table cfops_sem_validacao is
    'CFOPs marcados pelo analista como "não precisa validar" para uma empresa. Itens com esses CFOPs são '
    'ignorados pelas 3 validações automáticas de inconsistência (gerar_inconsistencias_ncm, '
    'gerar_inconsistencias_transferencia, gerar_inconsistencias_ncm_tributado) nesta e nas próximas '
    'competências, até o cadastro ser removido. Não afeta a Planilha nem a Apuração — só as validações.';

alter table cfops_sem_validacao enable row level security;
drop policy if exists "authenticated_full_access" on cfops_sem_validacao;
create policy "authenticated_full_access" on cfops_sem_validacao
    for all to authenticated using (true) with check (true);
