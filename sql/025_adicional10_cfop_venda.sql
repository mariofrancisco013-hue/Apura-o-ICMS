-- Adicional 10% — ajuste de CFOP para o botão "Faturamento (CFOP Venda)" — pedido do usuário em 14/08/2026:
-- "quanto ao faturamento quero que crie um botão do CFOP venda da aba ICMS Normal. Aí pode ser trazido
-- diretamente de lá como também pode ser digitado manualmente" + "mais deixar margem para excluir ou
-- incluir algum CFOP".
--
-- Regra automática (escolhida pelo usuário entre as opções apresentadas): soma o Valor Total das Saídas da
-- competência de ICMS Normal (mesma empresa/ano/mês) cujo CFOP tem "VENDA" na descrição oficial. Esta
-- tabela guarda os AJUSTES manuais por empresa — um CFOP aqui força incluir (`incluir = true`) ou excluir
-- (`incluir = false`) independente do texto da descrição, valendo mais que a regra automática. Mesmo
-- padrão de `cfops_sem_validacao` (sql/007 e seguintes — ver app/lib/cfops_sem_validacao.py).
create table if not exists icms_adicional10_cfop_venda_ajuste (
    id                  bigserial primary key,
    empresa_id          bigint not null references empresas(id) on delete cascade,
    cfop                integer not null references cfop(codigo),
    incluir             boolean not null,
    motivo              text,
    criado_por          uuid references auth.users(id),
    criado_por_email    text,
    created_at          timestamptz not null default now(),
    unique (empresa_id, cfop)
);
create index if not exists ix_icms_adicional10_cfop_venda_ajuste_empresa
    on icms_adicional10_cfop_venda_ajuste(empresa_id);

alter table icms_adicional10_cfop_venda_ajuste enable row level security;
drop policy if exists "authenticated_full_access" on icms_adicional10_cfop_venda_ajuste;
create policy "authenticated_full_access" on icms_adicional10_cfop_venda_ajuste
    for all to authenticated using (true) with check (true);
