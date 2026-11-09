# ZF 8HP TCU Inspector (`zf-8hp-tcu-inspector`)

Ferramenta em Python para auditoria diagnóstica e telemetria de unidades de controle de transmissão (TCU/EGS) **ZF 8HP45** via protocolo UDS sobre barramento CAN (ISO-TP).

## 📌 Visão Geral

Desenvolvido para varreduras em mecatrônicas ZF 8HP45 (bancada ou veículo) via adaptadores `slcan` (uCAN, PCAN, CANable). Extrai dados 100% dinâmicos do hardware e gera relatórios em `.txt` e `.json`.

### Recursos Principais
* **Identificação:** Extração de VIN (`0xF190`).
* **Mineração de Odômetro:** Varredura em DIDs e *Freeze Frames / Extended Data* (`0x19`).
* **Saúde das Embreagens (*Lernwerte*):** Leitura de offsets de pressão (`mbar`) e tempo (`ms`) dos 5 elementos (A, B, C, D, E).
* **Análise de DTCs:** Leitura e categorização funcional do histórico de falhas.

## 🤖 Desenvolvimento Auxiliado por IA

Este projeto foi **construído por meio de engenharia orientada a diálogo com Inteligência Artificial (Gemini / Google AI)**.

### Metodologia & Rodadas de Testes:
1. **Modelagem UDS / ISO-TP:** Implementação de Single/Multi-Frame e suporte a *Response Pending* (`0x78`).
2. **Purga de Dados Hardcoded:** Substituição de contadores estáticos por mineração dinâmica nos blocos de erro (`0x19`).
3. **Validação em Hardware:** 
   * *Rodada 1:* Identificação de VIN em bancada.
   * *Rodada 2:* Leitura de adaptações e detecção de estado crítico na Embreagem E (`+49.0 ms`).
   * *Rodada 3:* Confirmação de odômetro real via *Freeze Frame* (**54.658 km**) e categorização de 108 DTCs.

## 🚀 Como Utilizar

```bash
# Clone e instalação
git clone https://github.com/seu-usuario/zf-8hp-tcu-inspector.git
cd zf-8hp-tcu-inspector
pip install python-can

# Execução (configure a PORTA_COM no script)
python drivetcu_zf_8hp_reader.py
```

## ⚖️ Licença e Isenção de Responsabilidade

Disponibilizado sob a **Licença MIT**.

> **ISENÇÃO DE RESPONSABILIDADE:** Este código não possui qualquer garantia de funcionamento. O uso é de sua inteira responsabilidade e risco. Os autores não se responsabilizam por danos em mecatrônicas, veículos ou qualquer outro componente decorrentes de sua utilização.
