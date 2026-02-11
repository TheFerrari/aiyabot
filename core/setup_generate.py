import requests
import os
import sys
from tqdm import tqdm
from requests.exceptions import RequestException
import urllib3

SSL_VERIFY = os.getenv("AIYA_HF_SSL_VERIFY", "true").strip().lower() not in ("0", "false", "no", "off")
if not SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    print("[WARN] SSL verification disabled for model downloads (AIYA_HF_SSL_VERIFY=false).")

def download_with_tqdm(url, filepath):
    response = requests.get(url, stream=True, timeout=120, verify=SSL_VERIFY)
    response.raise_for_status()
    total = int(response.headers.get('content-length', 0))
    if total == 0:
        print(f"Downloading {os.path.basename(filepath)}…")
        with open(filepath, 'wb') as file:
            for chunk in response.iter_content(1024):
                if chunk:
                    file.write(chunk)
        return
    with open(filepath, 'wb') as file, tqdm(
        desc=f"Downloading {os.path.basename(filepath)}",
        total=total,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
        leave=True
    ) as bar:
        for data in response.iter_content(1024):
            size = file.write(data)
            bar.update(size)

def ensure_files(model_name, model_folder, base_url, files):
    os.makedirs(model_folder, exist_ok=True)
    errors = []

    for file in files:
        filepath = os.path.join(model_folder, file)
        if os.path.isfile(filepath):
            continue
        print(f"Missing file for {model_name}: {file}")
        url = base_url + file
        try:
            download_with_tqdm(url, filepath)
        except RequestException as e:
            print(f"[ERROR] No se pudo descargar {file} desde {url}")
            print(f"[ERROR] Motivo de red/SSL: {e}")
            errors.append(file)
        except Exception as e:
            print(f"[ERROR] Fallo inesperado descargando {file}: {e}")
            errors.append(file)

    return errors


# ----------- WizzGPTv6 -----------
model_folder_1 = "core/WizzGPT6"
base_url_1 = "https://huggingface.co/Wizz13150/WizzGPTv6/resolve/main/"
files_to_download_1 = [
    "config.json", "generation_config.json", "merges.txt", "pytorch_model.bin",
    "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.json",
    "WizzGPTv6.Q8_0.gguf"
]
errors_1 = ensure_files("WizzGPTv6", model_folder_1, base_url_1, files_to_download_1)

# ----- Meta-Llama-3.1-8B-Instruct-abliterated GGUF -----
model_folder_2 = "core/Meta-Llama-3.1-8B-Instruct-abliterated-gguf"
base_url_2 = "https://huggingface.co/mlabonne/Meta-Llama-3.1-8B-Instruct-abliterated-GGUF/resolve/main/"
files_to_download_2 = ["meta-llama-3.1-8b-instruct-abliterated.Q4_K_M.gguf"]
errors_2 = ensure_files(
    "Meta-Llama-3.1-8B-Instruct-abliterated-gguf",
    model_folder_2,
    base_url_2,
    files_to_download_2,
)

missing_after = []
for file in files_to_download_1:
    if not os.path.isfile(os.path.join(model_folder_1, file)):
        missing_after.append(os.path.join(model_folder_1, file))
for file in files_to_download_2:
    if not os.path.isfile(os.path.join(model_folder_2, file)):
        missing_after.append(os.path.join(model_folder_2, file))

if missing_after:
    print("\n[ERROR] Faltan archivos de modelo obligatorios:")
    for path in missing_after:
        print(f" - {path}")
    print("\n[ERROR] Soluciona conectividad/SSL con huggingface.co y vuelve a ejecutar setup_generate.py.")
    sys.exit(1)

print("All required generate/chat models are available.")
