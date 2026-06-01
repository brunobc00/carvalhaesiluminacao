import sys
import os
import markdown
import json
from weasyprint import HTML, CSS
from pathlib import Path
from bs4 import BeautifulSoup
import re
from datetime import datetime

def gerar_pdf(md_path):
    # Caminhos base
    base_dir = Path(md_path).parent
    repo_root = Path(__file__).parent.parent
    output_pdf = base_dir / f"{Path(md_path).stem}.pdf"
    
    # Carrega dados da empresa
    with open(repo_root / "dados_empresa.json", "r") as f:
        empresa = json.load(f)
    
    # Lê o Markdown
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()
    
    # Converte MD para HTML inicial
    html_raw = markdown.markdown(md_content, extensions=['tables'])
    
    # Extrai o nome do prédio ou cliente para a assinatura
    building_name = "ASSINATURA DO CLIENTE"
    first_line = md_content.split('\n')[0]
    match = re.search(r"# .* - (.*)", first_line)
    if match:
        building_name = match.group(1).strip()
    else:
        client_match = re.search(r"\*\*CLIENTE:\*\* (.*)", md_content)
        if client_match:
            building_name = client_match.group(1).strip()
            
    current_date = datetime.now().strftime("%d/%m/%Y")
    
    # Pós-processamento com BeautifulSoup para estilizar a tabela
    soup = BeautifulSoup(html_raw, 'html.parser')
    tables = soup.find_all('table')
    
    for table in tables:
        rows = table.find_all('tr')
        last_ambiente = None
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) > 0:
                current_ambiente = cells[0].get_text().strip()
                if last_ambiente and current_ambiente != last_ambiente:
                    row['class'] = row.get('class', []) + ['new-ambiente']
                last_ambiente = current_ambiente

    # Limpa as assinaturas do Markdown se existirem e substitui por HTML estilizado
    html_refined = str(soup)
    if "Confirmação de Pedido" in html_refined:
        html_refined = html_refined.split("Confirmação de Pedido")[0]
    
    signatures_html = f"""
    <div style="page-break-inside: avoid; margin-top: 50px;">
        <h2 style="border:none; background:none; text-align:center;">Confirmação de Pedido</h2>
        <p style="text-align:center; font-size: 9px; margin-bottom: 40px;">Confirmo os valores, condições de pagamentos e quantidades dos produtos acima relacionados.</p>
        <table class="signature-table">
            <tr>
                <td style="border:none; width: 45%;">
                    <div class="signature-line">
                        {empresa['nome_fantasia']}<br>
                        DATA: {current_date}
                    </div>
                </td>
                <td style="border:none; width: 10%;"></td>
                <td style="border:none; width: 45%;">
                    <div class="signature-line">
                        {building_name}<br>
                        DATA: {current_date}
                    </div>
                </td>
            </tr>
        </table>
    </div>
    """

    # Procura imagens na pasta do cliente
    images_html = ""
    valid_extensions = ('.jpg', '.jpeg', '.png', '.gif')
    image_files = [f for f in os.listdir(base_dir) if f.lower().endswith(valid_extensions)]
    
    if image_files:
        images_html = '<div class="gallery"><h2>Galeria de Fotos</h2>'
        for img in image_files:
            img_path = (base_dir / img).as_uri()
            images_html += f'<div class="gallery-item"><img src="{img_path}"><p>{img}</p></div>'
        images_html += '</div>'

    # Procura o logo na pasta assets
    logo_path = repo_root / "assets" / "logo_carvalhaes_comercial.png"
    logo_html = ""
    if logo_path.exists():
        logo_uri = logo_path.as_uri()
        logo_html = f'<div class="header-logo"><img src="{logo_uri}"></div>'

    # Template HTML final
    full_html = f"""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <title>Orçamento</title>
    </head>
    <body>
        <div class="header-container">
            {logo_html}
        </div>
        
        {html_refined}
        
        {signatures_html}
        
        {images_html}
    </body>
    </html>
    """
    
    # Aplica o CSS
    css_path = repo_root / "templates" / "orcamento.css"
    
    # Gera o PDF
    HTML(string=full_html, base_url=str(base_dir)).write_pdf(
        str(output_pdf),
        stylesheets=[CSS(filename=str(css_path))]
    )
    
    print(f"✅ PDF gerado com sucesso: {output_pdf}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python gerar_orcamento.py <caminho_do_arquivo.md>")
    else:
        gerar_pdf(sys.argv[1])
