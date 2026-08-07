-- Módulo novo: Apuração ICMS PE (regime de Crédito Presumido do atacadista, Decreto de PE) — pedido do
-- usuário em 07/08/2026, primeira empresa: Ultra Comércio Atacadista (filial Recife, CNPJ
-- 38.184.070/0002-09, IE 0996207-74). Modelo de cálculo bem diferente do ICMS Normal (Sodine): usa
-- Antecipação (border tax) + Crédito Presumido em vez de débito/crédito por CFOP is_st puro. Fonte de
-- dados: Rotina 1024 (mesmo PDF já usado no ICMS Normal, reaproveitado) + Extrato de ICMS Antecipado do
-- e-Fisco/PE (documento novo, específico desse regime). `competencias.modulo` já previa o valor
-- 'icms_antecipado' desde o schema original — usamos ele aqui, sem precisar alterar o CHECK.

-- 1) checkpoints_referencia ganha "Valores Contábeis" (coluna do Rotina 1024 que o ICMS Normal não usava
--    — ver comentário em app/lib/importar_1024.py). Confirmado com os dados reais de 06/2026 da Ultra
--    Comércio: as bases por CFOP da planilha de apuração PE (ex: "CFOP 1102" = 259.032,24) batem exatamente
--    com a coluna "Valores Contábeis" do Rotina 1024 desse CFOP, não com "Base de Cálculo".
alter table checkpoints_referencia
    add column if not exists valor_contabil numeric(14,2);
comment on column checkpoints_referencia.valor_contabil is
    '"Valores Contábeis" do Rotina 1024 (valor total da operação, antes de qualquer exclusão de base ICMS) '
    '— usado pela Apuração ICMS PE (Crédito Presumido) como base das linhas de Antecipação. Não usado pelo '
    'ICMS Normal (que usa valor_base/valor_icms, vindos da "Base de Cálculo"/"Imposto Creditado ou '
    'Debitado").';

-- 2) Cadastro por empresa de quais CFOPs entram na base de Antecipação, e se são "interna" (mesmo estado,
--    1,1%) ou "externa" (outro estado, calculado a partir do Extrato do e-Fisco) — mesmo padrão de
--    ncms_tributados/cfops_sem_validacao (cadastro editável pela tela, cresce por decisão do analista).
create table if not exists cfops_antecipacao_pe (
    id                bigserial primary key,
    empresa_id        bigint not null references empresas(id) on delete cascade,
    cfop              integer not null references cfop(codigo),
    bucket            text not null check (bucket in ('interna', 'externa')),
    observacao        text,
    criado_por        uuid references auth.users(id),
    criado_por_email  text,
    created_at        timestamptz not null default now(),
    unique (empresa_id, cfop)
);
create index if not exists ix_cfops_antecipacao_pe_empresa on cfops_antecipacao_pe(empresa_id);
comment on table cfops_antecipacao_pe is
    'CFOPs de Entrada que compõem a base da Antecipação na Apuração ICMS PE (Crédito Presumido) — '
    '"interna" soma na linha 3.1 (calculada a 1,1% do total), "externa" soma na linha 3.2 (cujo valor real '
    'vem do Extrato de ICMS Antecipado do e-Fisco, não de uma alíquota fixa). Seed inicial pra Ultra '
    'Comércio confirmado contra a planilha de apuração real de 06/2026.';

insert into cfops_antecipacao_pe (empresa_id, cfop, bucket, observacao)
select e.id, x.cfop, x.bucket, 'Seed inicial 07/08/2026, a partir da planilha de apuração PE existente'
from empresas e
cross join (values
    (1102, 'interna'), (1403, 'interna'), (1910, 'interna'),
    (2102, 'externa'), (2403, 'externa'), (2409, 'externa'), (2949, 'externa'),
    (2117, 'externa'), (2118, 'externa'), (2910, 'externa'), (6202, 'externa'), (5202, 'externa')
) as x(cfop, bucket)
where e.cnpj = '38.184.070/0002-09'
on conflict (empresa_id, cfop) do nothing;

-- 3) Grupos de mercadoria do Extrato de ICMS Antecipado (e-Fisco/PE) — um registro por grupo, por
--    competência, guardado pra auditoria (de onde veio o valor da linha 3.2) e pra recalcular o total
--    (soma de icms_devido onde direito_credito = true) sem reabrir o PDF.
create table if not exists extrato_antecipado_pe (
    id                bigserial primary key,
    competencia_id    bigint not null references competencias(id) on delete cascade,
    grupo_mercadoria  text not null,
    direito_credito   boolean not null,
    icms_devido       numeric(14,2) not null,
    created_at        timestamptz not null default now()
);
create index if not exists ix_extrato_antecipado_pe_competencia on extrato_antecipado_pe(competencia_id);
comment on table extrato_antecipado_pe is
    'Um registro por "Grupo de Mercadoria" do quadro Resumo do Extrato de ICMS Antecipado (e-Fisco/PE), por '
    'competência — ex: ANTECIPACAO, SUBST.TRIB(COSMET.). A linha 3.2 (Antecipação externa) da Apuração '
    'ICMS PE soma icms_devido só dos grupos com direito_credito=true (confirmado com o usuário em '
    '07/08/2026 e batendo exato com os dados reais de 06/2026 da Ultra Comércio).';

-- 4) checkpoints_referencia ganha uma terceira "fonte": 'manual_pe' — usada só pela linha 4.1.01 ("Valor
--    Total das Saídas ajustado"), a única linha da Apuração ICMS PE que não dá pra derivar com certeza só
--    com os dados da Rotina 1024 (ver docstring de app/lib/calculo_icms_pe.py — o valor real de 06/2026
--    da Ultra Comércio ficou R$ 30.828,00 abaixo do valor "cru" calculado a partir dos CFOPs, diferença que
--    bate exatamente com um ajuste de reclassificação contábil que não aparece em nenhum CFOP do Rotina
--    1024). Guardada com linha='4.1.01' e valor_icms=valor digitado, mesmo padrão da fonte 'rotina_1025'.
alter table checkpoints_referencia drop constraint if exists checkpoints_referencia_fonte_check;
alter table checkpoints_referencia add constraint checkpoints_referencia_fonte_check
    check (fonte in ('rotina_1024', 'rotina_1025', 'manual_pe'));

alter table cfops_antecipacao_pe enable row level security;
alter table extrato_antecipado_pe enable row level security;
do $$
declare
    t text;
begin
    for t in select unnest(array['cfops_antecipacao_pe', 'extrato_antecipado_pe'])
    loop
        execute format('drop policy if exists "authenticated_full_access" on %I', t);
        execute format(
            'create policy "authenticated_full_access" on %I '
            'for all to authenticated using (true) with check (true)', t
        );
    end loop;
end $$;
