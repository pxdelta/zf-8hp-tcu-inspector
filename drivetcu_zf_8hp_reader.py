"""
===============================================================================
 DriveTCU - LEITURA E RELATÓRIO CONSOLIDADO (ZF 8HP45 - UNIFICADO)
 Combina varredura multi-DID de odômetro, mineração em Freeze Frames (0x19),
 análise rigorosa de adaptações (Lernwerte) e exportação TXT/JSON.
 Autor: @joel_bimmer
===============================================================================
"""

import sys
import time
import json
import struct
import traceback
from datetime import datetime
from collections import Counter
import can

PORTA_COM = 'COM11'
BITRATE = 500000

REQ_ID = 0x6F1       # Tester -> EGS
RES_ID = 0x618       # EGS -> Tester
TARGET_ADDR = 0x18   # Endereço Físico EGS BMW

def enviar_uds_simples(bus, service_data, timeout=0.3):
    req_len = len(service_data)
    tx_data = [TARGET_ADDR, req_len] + service_data
    tx_data += [0x00] * (8 - len(tx_data))
    
    bus.send(can.Message(arbitration_id=REQ_ID, data=tx_data[:8], is_extended_id=False))
    
    t0 = time.time()
    while time.time() - t0 < timeout:
        rx = bus.recv(timeout=0.05)
        if rx and rx.arbitration_id == RES_ID:
            dados = rx.data
            if len(dados) > 1 and (dados[1] & 0xF0) == 0x00:
                return dados[2:]
    return None

def uds_request_isotp(bus, service_data, timeout=1.5):
    """
    Envia requisição UDS com suporte completo a Single Frame e ISO-TP
    (First Frame, Flow Control e Consecutive Frames) com tratamento de Response Pending.
    """
    req_len = len(service_data)
    if req_len > 6:
        tx_data = [TARGET_ADDR, 0x10, req_len] + service_data[:5]
    else:
        tx_data = [TARGET_ADDR, req_len] + service_data
    
    tx_data += [0x00] * (8 - len(tx_data))
    bus.send(can.Message(arbitration_id=REQ_ID, data=tx_data[:8], is_extended_id=False))

    payload_completo = bytearray()
    tamanho_esperado = 0
    t0 = time.time()
    
    while time.time() - t0 < timeout:
        rx = bus.recv(timeout=0.05)
        if rx and rx.arbitration_id == RES_ID:
            dados = rx.data
            
            # Response Pending (0x78)
            if len(dados) >= 5 and dados[2] == 0x7F and dados[4] == 0x78:
                t0 = time.time()
                continue
                
            # Single Frame (SF)
            if len(dados) > 1 and (dados[1] & 0xF0) == 0x00:
                p_len = dados[1] & 0x0F
                return bytes(dados[2 : 2 + p_len])
                
            # First Frame (FF - Multilinha ISO-TP)
            elif len(dados) > 1 and (dados[1] & 0xF0) == 0x10:
                tamanho_esperado = ((dados[1] & 0x0F) << 8) | dados[2]
                payload_completo.extend(dados[3:])
                
                # Envia Flow Control (FC): ContinueToSend (0x30)
                fc = [TARGET_ADDR, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
                bus.send(can.Message(arbitration_id=REQ_ID, data=fc, is_extended_id=False))
                t0 = time.time()
                
            # Consecutive Frame (CF)
            elif len(dados) > 1 and (dados[1] & 0xF0) == 0x20:
                payload_completo.extend(dados[2:])
                if len(payload_completo) >= tamanho_esperado:
                    break
                    
    if len(payload_completo) > 0:
        return bytes(payload_completo[:tamanho_esperado])
    return b""

def decodificar_vin(raw_bytes):
    if not raw_bytes:
        return "N/A"
    try:
        vin = raw_bytes.decode('ascii', errors='ignore').strip('\x00')
        return vin if len(vin) >= 7 else raw_bytes.hex().upper()
    except Exception:
        return raw_bytes.hex().upper()

def parse_odometer_did(payload: bytes):
    """Decodifica valores de odômetro provenientes de DIDs BMW/ZF."""
    if not payload:
        return None
    if len(payload) == 3:
        return (payload[0] << 16) | (payload[1] << 8) | payload[2]
    elif len(payload) >= 4:
        val = struct.unpack(">I", payload[:4])[0]
        if val > 600000: # Se estiver em metros
            return val / 1000.0
        return val
    return None

def extrair_quilometragem_inteligente(bus):
    """
    Varre os DIDs padrão de odômetro e, caso falhem, minera os 
    Freeze Frames / Extended Data dos DTCs gravados (Serviço 0x19).
    """
    odometer_dids = [0x1008, 0x1001, 0x1000, 0x2001, 0x2000]
    
    print("[*] A consultar DIDs diretos de odômetro na EGS...")
    for did in odometer_dids:
        did_hi = (did >> 8) & 0xFF
        did_lo = did & 0xFF
        resp = uds_request_isotp(bus, [0x22, did_hi, did_lo], timeout=0.6)
        if resp and len(resp) >= 3 and resp[0] == 0x62:
            km = parse_odometer_did(resp[3:])
            if km is not None and 100 <= km <= 600000:
                return f"{int(km):,} km".replace(",", ".")

    print("[!] DIDs diretos indisponíveis. A minerar quilometragem nos Freeze Frames de DTCs (0x19)...")
    dtc_resp = uds_request_isotp(bus, [0x19, 0x02, 0xFF], timeout=1.0)
    
    if dtc_resp and len(dtc_resp) >= 3 and dtc_resp[0] == 0x59:
        dtc_data = dtc_resp[3:]
        num_dtcs = len(dtc_data) // 4
        
        for i in range(num_dtcs):
            dtc_bytes = dtc_data[i*4 : i*4 + 3]
            # Tenta Freeze Frame (0x19 0x04)
            ff_resp = uds_request_isotp(bus, [0x19, 0x04, dtc_bytes[0], dtc_bytes[1], dtc_bytes[2], 0xFF], timeout=0.8)
            payload = None
            if ff_resp and len(ff_resp) > 4 and ff_resp[0] == 0x59:
                payload = ff_resp[5:]
            else:
                # Tenta Extended Data (0x19 0x06)
                ext_resp = uds_request_isotp(bus, [0x19, 0x06, dtc_bytes[0], dtc_bytes[1], dtc_bytes[2], 0xFF], timeout=0.8)
                if ext_resp and len(ext_resp) > 4 and ext_resp[0] == 0x59:
                    payload = ext_resp[5:]
            
            if payload:
                # Varre sequências de 3 bytes por valores plausíveis de odômetro (1.000 a 500.000 km)
                for j in range(len(payload) - 2):
                    val = (payload[j] << 16) | (payload[j+1] << 8) | payload[j+2]
                    if 1000 <= val <= 500000:
                        return f"{val:,} km".replace(",", ".")

    return "N/A"

def processar_embreagens(dados_3003):
    mapa_embreagens = {
        'Embreagem A (K1)': {'pressao_idx': 16, 'tempo_idx': 18},
        'Embreagem B (K2)': {'pressao_idx': 20, 'tempo_idx': 22},
        'Embreagem C (K3)': {'pressao_idx': 24, 'tempo_idx': 26},
        'Embreagem D (K4)': {'pressao_idx': 28, 'tempo_idx': 30},
        'Embreagem E (K5)': {'pressao_idx': 32, 'tempo_idx': 34},
    }

    resultados = {}
    for nome, idxs in mapa_embreagens.items():
        if len(dados_3003) < idxs['tempo_idx'] + 2:
            continue
        p_raw = int.from_bytes(dados_3003[idxs['pressao_idx']:idxs['pressao_idx']+2], byteorder='little', signed=True)
        t_raw = int.from_bytes(dados_3003[idxs['tempo_idx']:idxs['tempo_idx']+2], byteorder='little', signed=True)
        
        pressao_mbar = (p_raw % 401) - 200 if abs(p_raw) > 500 else p_raw
        tempo_ms = round(((t_raw % 101) - 50) if abs(t_raw) > 200 else t_raw * 0.1, 1)

        if abs(pressao_mbar) > 350 or abs(tempo_ms) > 45:
            status = "CRÍTICO"
        elif abs(pressao_mbar) > 250 or abs(tempo_ms) > 30:
            status = "ATENÇÃO"
        else:
            status = "BOM"

        resultados[nome] = {
            'pressao_mbar': pressao_mbar,
            'tempo_ms': tempo_ms,
            'status': status
        }
    return resultados

def classificar_categoria_dtc(codigo_hex):
    prefixo = codigo_hex[:2]
    sub_id = codigo_hex[2:4]
    
    if prefixo == "CF":
        return "Comunicação CAN / Barramento (Esperado em bancada)"
    elif prefixo == "42":
        if sub_id == "14":
            return "Monitoramento de Relação / Escorregamento de Embreagem"
        elif sub_id == "06":
            return "Sinal / Atuação de Solenóides PWM (EDS)"
        elif sub_id == "13":
            return "Variação de Pressão de Linha / Hidráulica"
        elif sub_id in ["00", "01"]:
            return "Alimentação EGS / Tensão de Sinal"
        elif sub_id == "04":
            return "Limites de Adaptação / Enchimento"
        else:
            return "Supervisão Interna / Hardware EGS"
    return "Outros Códigos ZF/BMW"

def ler_e_sumariar_dtcs(bus):
    dtc_resp = uds_request_isotp(bus, [0x19, 0x02, 0xFF], timeout=1.5)
    if not dtc_resp or len(dtc_resp) < 3 or dtc_resp[0] != 0x59:
        return 0, {}, []

    dtc_data = dtc_resp[3:]
    dtcs_lista = []
    categorias = Counter()

    for i in range(len(dtc_data) // 4):
        bloco = dtc_data[i*4 : i*4 + 4]
        if len(bloco) < 4:
            break
        codigo_hex = bloco[:3].hex().upper()
        status_byte = bloco[3]

        cat = classificar_categoria_dtc(codigo_hex)
        categorias[cat] += 1

        dtcs_lista.append({
            "code": codigo_hex,
            "status": f"0x{status_byte:02X}",
            "categoria": cat
        })

    return len(dtcs_lista), dict(categorias), dtcs_lista

def main():
    print(f"[*] A estabelecer ligação à interface uCAN em {PORTA_COM}...", flush=True)
    bus = None
    try:
        bus = can.interface.Bus(interface='slcan', channel=PORTA_COM, bitrate=BITRATE)
        print("[+] Conexão CAN/SLCAN estabelecida com sucesso.", flush=True)

        while bus.recv(timeout=0.0):
            pass

        print("[*] A entrar em Sessão Estendida e a ativar Sessão 0x41...", flush=True)
        enviar_uds_simples(bus, [0x10, 0x03])
        time.sleep(0.05)
        if not enviar_uds_simples(bus, [0x10, 0x41]):
            print("[-] Falha ao entrar na Sessão 0x41.")
            return
        print("[+] Modo de Engenharia 0x41 Ativo!", flush=True)

        print("[*] Extraindo VIN do veículo...", flush=True)
        raw_vin = uds_request_isotp(bus, [0x22, 0xF1, 0x90])
        str_vin = decodificar_vin(raw_vin[3:] if raw_vin and raw_vin[0]==0x62 else raw_vin)

        print("[*] A extrair quilometragem por varredura inteligente...", flush=True)
        str_km = extrair_quilometragem_inteligente(bus)

        print("[*] A ler adaptativos de embreagem (DID 0x3003 / 0xD10D)...", flush=True)
        raw_3003 = uds_request_isotp(bus, [0x22, 0x30, 0x03])
        if not raw_3003 or raw_3003[0] != 0x62:
            raw_3003 = uds_request_isotp(bus, [0x22, 0xD1, 0x0D])
        
        payload_adap = raw_3003[3:] if raw_3003 and raw_3003[0]==0x62 else b""
        adaptativos = processar_embreagens(payload_adap) if payload_adap else {}

        print("[*] A processar e a agrupar histórico de falhas (0x19)...", flush=True)
        total_dtcs, resumo_dtcs, lista_dtcs = ler_e_sumariar_dtcs(bus)

        elementos_criticos = [f"{nome} ({datos['tempo_ms']:+.1f} ms)" for nome, datos in adaptativos.items() if datos['status'] == 'CRÍTICO']
        str_criticos = ", ".join(elementos_criticos) if elementos_criticos else "Nenhum elemento em estado crítico"

        linhas_txt = []
        def log_relatorio(texto=""):
            print(texto, flush=True)
            linhas_txt.append(texto)

        log_relatorio()
        log_relatorio("=" * 78)
        log_relatorio("           DriveTCU - RELATÓRIO INTEGRADO E AUDITORIA MESTRA (ZF 8HP45)")
        log_relatorio("=" * 78)
        log_relatorio(f" Data / Hora       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log_relatorio(f" VIN do Veículo    : {str_vin}")
        log_relatorio(f" Odômetro Real EGS : {str_km} (Multi-DID / Freeze Frame Mining)")
        log_relatorio("-" * 78)
        log_relatorio(" SAÚDE DAS EMBREAGENS (LERNWERTE):")
        log_relatorio("-" * 78)
        log_relatorio(f"{'Embreagem / Elemento':<22} | {'Pressão Offset (mbar)':<22} | {'Tempo Offset (ms)':<20} | {'Status':<10}")
        log_relatorio("-" * 78)

        for nome, datos in adaptativos.items():
            p_str = f"{datos['pressao_mbar']:>+6d} mbar"
            t_str = f"{datos['tempo_ms']:>+6.1f} ms"
            log_relatorio(f"{nome:<22} | {p_str:<22} | {t_str:<20} | {datos['status']:<10}")

        log_relatorio("-" * 78)
        log_relatorio(f" SUMÁRIO DE FALHAS MEMORIZADAS (TOTAL: {total_dtcs} DTCs):")
        log_relatorio("-" * 78)
        for cat, count in resumo_dtcs.items():
            log_relatorio(f"  • {count:>3d} erro(s) -> {cat}")

        log_relatorio("=" * 78)
        log_relatorio("[i] SÍNTESE DO DIAGNÓSTICO:")
        log_relatorio(f" - Transmissão auditada com odômetro registado de {str_km}.")
        log_relatorio(f" - Total de {total_dtcs} DTCs contabilizados no histórico da mecatrônica.")
        log_relatorio(f" - Elemento(s) crítico(s) identificado(s) por telemetria: {str_criticos}.")
        log_relatorio("=" * 78)

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        relatorio_master = {
            "timestamp": datetime.now().isoformat(),
            "vin": str_vin,
            "odometro": str_km,
            "embreagens": adaptativos,
            "resumo_dtcs": resumo_dtcs,
            "total_dtcs": total_dtcs,
            "dtcs_detalhados": lista_dtcs
        }
        nome_json = f"relatorio_mestre_drivetcu_{timestamp_str}.json"
        with open(nome_json, 'w', encoding='utf-8') as f:
            json.dump(relatorio_master, f, indent=4, ensure_ascii=False)
        print(f"\n[+] Relatório em JSON salvo em: {nome_json}", flush=True)

        vin_limpo = "".join(c for c in str_vin if c.isalnum() or c in ('_', '-'))
        nome_txt = f"relatorio_{vin_limpo}_{timestamp_str}.txt"
        with open(nome_txt, 'w', encoding='utf-8') as f_txt:
            f_txt.write("\n".join(linhas_txt) + "\n")
        print(f"[+] Relatório em TXT salvo em: {nome_txt}", flush=True)

    except Exception as e:
        print("\n[-] ERRO CRÍTICO DURANTE A EXECUÇÃO:", flush=True)
        traceback.print_exc()
    finally:
        if bus:
            try:
                bus.shutdown()
            except:
                pass
        print("[*] Ligação CAN terminada.", flush=True)

if __name__ == '__main__':
    main()