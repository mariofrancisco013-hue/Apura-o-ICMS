-- Aba Interestadual (ICMS Substituição) — pedido do usuário em 11/08/2026: "colocar uma observação de
-- situação, para informar se alguma nota é de outra competência E ela não deve ir para o cálculo".
--
-- Campo separado da Justificativa/Observação (sql/018): um checkbox — quando marcado, a NF sai da
-- contagem de Pendentes/Divergentes/Não localizadas (fica só na contagem "Excluídas do cálculo"). Cobre
-- qualquer situação da aba, não só Divergente/Não localizado — ex: uma NF "Pendente de entrada" que na
-- verdade é de outra competência (lançamento da SEFAZ referente a um mês anterior/posterior) também não
-- deveria contar como pendência real deste mês.

alter table icms_st_justificativas
    add column if not exists nao_entra_calculo boolean not null default false;
