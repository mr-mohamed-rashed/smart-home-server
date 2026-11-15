import os
import sys
import json
import time
import socket
import subprocess
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from PIL import Image

# دعم ملفات PDF عبر PyMuPDF
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

app = Flask(__name__)
app.secret_key = "smart_home_server_secret_key_2026"

# المسارات الأساسية
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
SHARED_FOLDER = os.path.join(BASE_DIR, "shared_files")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SHARED_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SHARED_FOLDER'] = SHARED_FOLDER

def load_settings():
    """تحميل إعدادات السيرفر مثل IP الطابعة"""
    default_settings = {
        "printer_ip": "192.168.100.43",
        "printer_model": "Ricoh Aficio SP C252SF"
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return {**default_settings, **json.load(f)}
        except Exception:
            return default_settings
    return default_settings

def save_settings(settings):
    """حفظ الإعدادات"""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def get_local_ip_and_subnet():
    """اكتشاف عنوان IP الخاص بجهاز اللاب والنطاق تلقائياً"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split('.')
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}."
        return local_ip, subnet
    except Exception:
        return "127.0.0.1", "192.168.100."

def check_printer_online(ip):
    """التحقق من اتصال الطابعة عبر منفذ 80 أو 9100"""
    for port in [80, 9100, 631]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.8)
            result = sock.connect_ex((ip, port))
            sock.close()
            if result == 0:
                return True
        except Exception:
            pass
    try:
        res = subprocess.run(f"ping -n 1 -w 400 {ip}", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)
        return res.returncode == 0
    except Exception:
        return False

def parse_page_range(selection_type, custom_str, total_pages):
    """تحليل أرقام الصفحات المطلوبة بدقة"""
    clean_custom = custom_str.strip().replace("،", ",")
    if clean_custom and selection_type in ['all', 'custom']:
        selection_type = 'custom'

    if selection_type == 'odd':
        return [i for i in range(total_pages) if (i + 1) % 2 != 0]
    elif selection_type == 'even':
        return [i for i in range(total_pages) if (i + 1) % 2 == 0]
    elif selection_type == 'custom' and clean_custom:
        pages = set()
        for part in clean_custom.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    start_str, end_str = part.split("-", 1)
                    start = max(1, int(start_str.strip()))
                    end = min(total_pages, int(end_str.strip()))
                    if start <= end:
                        for p in range(start, end + 1):
                            pages.add(p - 1)
                except Exception:
                    pass
            else:
                try:
                    p = int(part)
                    if 1 <= p <= total_pages:
                        pages.add(p - 1)
                except Exception:
                    pass
        res = sorted(list(pages))
        return res if res else list(range(total_pages))
    else:
        return list(range(total_pages))

# ==================== محرك بناء أوامر الطباعة المتقدمة (مع الهامش الصفري بدون حواف) ====================

def build_advanced_print_job(images, paper_size='A4', orientation='auto', color_mode='color', 
                             paper_type='plain', fit_mode='fit', scale_percent=100, margin_mm=0, copies=1, duplex='off'):
    """
    بناء دفق طباعة متكامل (PJL + PostScript 3) مع دعم:
    - إزالة الحواف تماماً (Borderless / 0mm Margin)
    - التمديد الحر (Stretch) وتغيير نسبة الحجم (Scale %)
    - الطباعة وش وضهر (Duplex)
    - مقاس الورقة ونمط الألوان
    """
    sizes = {
        'A4': (595, 842),
        'Letter': (612, 792),
        'A5': (420, 595)
    }
    base_w, base_h = sizes.get(paper_size, (595, 842))
    
    # تحويل المليمتر إلى نقاط PostScript (1 mm = 2.835 points)
    margin = float(margin_mm) * 2.835

    pjl_media = {
        'plain': 'REGULAR',
        'thick': 'THICK',
        'glossy': 'GLOSSY',
        'recycled': 'RECYCLED'
    }.get(paper_type, 'REGULAR')

    pjl_color = 'COLOR' if color_mode == 'color' else 'MONO'

    # إعدادات الطباعة وش وضهر (Duplex)
    if duplex == 'long_edge':
        pjl_duplex = '@PJL SET DUPLEX = ON\r\n@PJL SET BINDING = LONGEDGE\r\n'
        ps_duplex = '<</Duplex true /Tumble false>> setpagedevice\r\n'
    elif duplex == 'short_edge':
        pjl_duplex = '@PJL SET DUPLEX = ON\r\n@PJL SET BINDING = SHORTEDGE\r\n'
        ps_duplex = '<</Duplex true /Tumble true>> setpagedevice\r\n'
    else:
        pjl_duplex = '@PJL SET DUPLEX = OFF\r\n'
        ps_duplex = '<</Duplex false>> setpagedevice\r\n'

    stream = []
    stream.append('\x1b%-12345X@PJL\r\n')
    stream.append('@PJL JOB NAME = "SmartHomePrint"\r\n')
    stream.append(f'@PJL SET COPIES = {copies}\r\n')
    stream.append(f'@PJL SET PAPER = {paper_size.upper()}\r\n')
    stream.append(f'@PJL SET MEDIATYPE = {pjl_media}\r\n')
    stream.append(f'@PJL SET COLORMODE = {pjl_color}\r\n')
    stream.append(pjl_duplex)
    stream.append('@PJL ENTER LANGUAGE = POSTSCRIPT\r\n')
    stream.append('%!PS-Adobe-3.0\r\n')
    stream.append('%%Creator: Smart Home Server Print Engine\r\n')
    stream.append(f'%%Pages: {len(images)}\r\n')
    stream.append(f'%%BoundingBox: 0 0 {base_w} {base_h}\r\n')
    stream.append(ps_duplex)
    stream.append('%%EndComments\r\n')

    scale_factor = max(0.2, min(2.5, scale_percent / 100.0))

    for idx, raw_img in enumerate(images):
        orig_w, orig_h = raw_img.size
        
        current_orient = orientation
        if current_orient == 'auto':
            current_orient = 'landscape' if orig_w > orig_h else 'portrait'

        if current_orient == 'landscape':
            pw, ph = max(base_w, base_h), min(base_w, base_h)
        else:
            pw, ph = min(base_w, base_h), max(base_w, base_h)

        if color_mode == 'mono':
            img = raw_img.convert('L')
            is_color = False
        else:
            img = raw_img.convert('RGB')
            is_color = True

        tw = max(10, pw - (2 * margin))
        th = max(10, ph - (2 * margin))

        # حساب الأبعاد وفق نمط الاسترتش ومقدار الهامش
        if fit_mode == 'stretch':
            dw = tw * scale_factor
            dh = th * scale_factor
        elif fit_mode == 'fill':
            s = max(tw / orig_w, th / orig_h) * scale_factor
            dw = orig_w * s
            dh = orig_h * s
        else:  # 'fit'
            s = min(tw / orig_w, th / orig_h) * scale_factor
            dw = orig_w * s
            dh = orig_h * s

        px = margin + (tw - dw) / 2
        py = margin + (th - dh) / 2

        if max(orig_w, orig_h) > 1500:
            resample_ratio = 1500 / max(orig_w, orig_h)
            img = img.resize((int(orig_w * resample_ratio), int(orig_h * resample_ratio)), Image.Resampling.LANCZOS)

        iw, ih = img.size
        hex_data = img.tobytes().hex()

        stream.append(f'%%Page: {idx + 1} {idx + 1}\r\n')
        if is_color:
            stream.append(f'/picstr {iw * 3} string def\r\n')
            stream.append(f'{px:.2f} {py:.2f} translate\r\n{dw:.2f} {dh:.2f} scale\r\n')
            stream.append(f'{iw} {ih} 8 [{iw} 0 0 -{ih} 0 {ih}] {{ currentfile picstr readhexstring pop }} false 3 colorimage\r\n')
        else:
            stream.append(f'/picstr {iw} string def\r\n')
            stream.append(f'{px:.2f} {py:.2f} translate\r\n{dw:.2f} {dh:.2f} scale\r\n')
            stream.append(f'{iw} {ih} 8 [{iw} 0 0 -{ih} 0 {ih}] {{ currentfile picstr readhexstring pop }} image\r\n')

        stream.append(hex_data)
        stream.append('\r\nshowpage\r\n')

    stream.append('%%EOF\r\n\x1b%-12345X@PJL EOJ\r\n\x1b%-12345X')
    return ''.join(stream).encode('latin1')

def send_to_raw_printer(ip, byte_payload):
    """إرسال مباشر لمنفذ RAW JetDirect 9100 لطابعة ريكو"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(12.0)
    s.connect((ip, 9100))
    s.sendall(byte_payload)
    s.close()

def ping_host(ip):
    """إرسال Ping سريع"""
    cmd = f"ping -n 1 -w 250 {ip}"
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)

def scan_arp_devices(subnet, my_ip, printer_ip):
    """قراءة الأجهزة النشطة على الشبكة"""
    ips = [f"{subnet}{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=35) as executor:
        executor.map(ping_host, ips)

    devices = []
    try:
        output = subprocess.check_output("arp -a", shell=True).decode('cp1256', errors='ignore')
        pattern = re.compile(rf"({re.escape(subnet)}\d+)\s+([0-9a-fA-F-]+)\s+dynamic", re.IGNORECASE)
        
        seen_ips = set()
        for match in pattern.finditer(output):
            ip = match.group(1)
            mac = match.group(2).upper()
            if ip in seen_ips:
                continue
            seen_ips.add(ip)

            is_router = ip.endswith(".1")
            is_me = (ip == my_ip)
            is_printer = (ip == printer_ip)

            try:
                name = socket.gethostbyaddr(ip)[0]
            except Exception:
                if is_router:
                    name = "راوتر الواي فاي (Router)"
                elif is_me:
                    name = "سيرفر البيت (هذا اللابتوب)"
                elif is_printer:
                    name = "طابعة ريكو (Ricoh SP C252SF)"
                else:
                    name = f"جهاز على الشبكة ({ip.split('.')[-1]})"

            devices.append({
                "ip": ip,
                "mac": mac,
                "name": name,
                "is_router": is_router,
                "is_me": is_me,
                "is_printer": is_printer
            })
    except Exception as e:
        print("خطأ أثناء قراءة جدول ARP:", e)

    devices.sort(key=lambda d: (not d["is_router"], not d["is_me"], not d["is_printer"], int(d["ip"].split('.')[-1])))
    return devices

def get_shared_files_list():
    """عرض قائمة الملفات المشتركة"""
    files = []
    for fname in os.listdir(SHARED_FOLDER):
        fpath = os.path.join(SHARED_FOLDER, fname)
        if os.path.isfile(fpath):
            stat = os.stat(fpath)
            size_mb = stat.st_size / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB" if size_mb >= 1 else f"{stat.st_size / 1024:.1f} KB"
            mod_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            files.append({"name": fname, "size": size_str, "time": mod_time})
    return files

# ==================== الروابط (ROUTES) ====================

@app.route('/')
def index():
    settings = load_settings()
    my_ip, subnet = get_local_ip_and_subnet()
    printer_online = check_printer_online(settings["printer_ip"])
    files = get_shared_files_list()
    
    cached_devices = getattr(app, 'cached_devices', [])
    if not cached_devices:
        cached_devices = [
            {"ip": f"{subnet}1", "mac": "DYNAMIC", "name": "راوتر الواي فاي (Router)", "is_router": True, "is_me": False, "is_printer": False},
            {"ip": my_ip, "mac": "THIS-PC", "name": "سيرفر البيت (هذا اللابتوب)", "is_router": False, "is_me": True, "is_printer": False},
            {"ip": settings["printer_ip"], "mac": "RICOH", "name": "طابعة ريكو (Ricoh SP C252SF)", "is_router": False, "is_me": False, "is_printer": True}
        ]

    return render_template('index.html',
                           server_ip=my_ip,
                           subnet=subnet,
                           printer_ip=settings["printer_ip"],
                           printer_online=printer_online,
                           devices=cached_devices,
                           files=files)

@app.route('/set_printer_ip', methods=['POST'])
def set_printer_ip():
    new_ip = request.form.get('new_ip', '').strip()
    if new_ip:
        settings = load_settings()
        settings['printer_ip'] = new_ip
        save_settings(settings)
        flash(f"تم تحديث عنوان IP الطابعة إلى: {new_ip}", "success")
    return redirect(url_for('index'))

@app.route('/print', methods=['POST'])
def handle_print():
    settings = load_settings()
    printer_ip = settings["printer_ip"]

    # قراءة كل خيارات وإعدادات الطباعة
    copies = int(request.form.get('copies', 1))
    paper_size = request.form.get('paper_size', 'A4')
    orientation = request.form.get('orientation', 'auto')
    color_mode = request.form.get('color_mode', 'color')
    paper_type = request.form.get('paper_type', 'plain')
    fit_mode = request.form.get('fit_mode', 'fit')
    scale_percent = int(request.form.get('scale_percent', 100))
    margin_mm = float(request.form.get('margin_mm', 0)) # الهوامش بالملم (0 = بدون حواف)
    duplex = request.form.get('duplex', 'off')

    # قراءة خيارات تحديد الصفحات
    page_selection = request.form.get('page_selection', 'all')
    custom_pages = request.form.get('custom_pages', '').strip()

    if 'file' not in request.files:
        flash("يرجى اختيار ملف للطباعة!", "warning")
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        flash("لم يتم اختيار أي ملف!", "warning")
        return redirect(url_for('index'))

    filename = f"{int(time.time())}_{file.filename}"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)

    ext = os.path.splitext(file.filename)[1].lower()

    # معالجة الصور
    if ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']:
        try:
            with Image.open(file_path) as img:
                images_list = [img.copy()]

            payload = build_advanced_print_job(
                images=images_list,
                paper_size=paper_size,
                orientation=orientation,
                color_mode=color_mode,
                paper_type=paper_type,
                fit_mode=fit_mode,
                scale_percent=scale_percent,
                margin_mm=margin_mm,
                copies=copies,
                duplex=duplex
            )
            
            send_to_raw_printer(printer_ip, payload)
            
            margin_desc = "بدون حواف (0mm)" if margin_mm == 0 else f"هامش {margin_mm}mm"
            flash(f"تمت طباعة الصورة بنجاح! [{margin_desc} | استرتش كامل | {copies} نسخة] ✅🖨️", "success")
        except Exception as e:
            flash(f"حدث خطأ أثناء طباعة الصورة: {str(e)}", "danger")

    # معالجة ملفات PDF
    elif ext == '.pdf':
        if not PDF_SUPPORT:
            try:
                for _ in range(copies):
                    os.startfile(file_path, "print")
                flash("تم إرسال مستند PDF للطباعة بنجاح!", "success")
            except Exception as e:
                flash(f"خطأ في طباعة PDF: {str(e)}", "danger")
        else:
            try:
                doc = fitz.open(file_path)
                total_pages = len(doc)
                
                selected_indices = parse_page_range(page_selection, custom_pages, total_pages)
                
                if not selected_indices:
                    flash("لم يتم تحديد أي صفحات صالحة من ملف PDF!", "warning")
                    return redirect(url_for('index'))

                images_list = []
                for idx in selected_indices:
                    page = doc.load_page(idx)
                    pix = page.get_pixmap(dpi=150)
                    images_list.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
                doc.close()

                payload = build_advanced_print_job(
                    images=images_list,
                    paper_size=paper_size,
                    orientation=orientation,
                    color_mode=color_mode,
                    paper_type=paper_type,
                    fit_mode=fit_mode,
                    scale_percent=scale_percent,
                    margin_mm=margin_mm,
                    copies=copies,
                    duplex=duplex
                )

                send_to_raw_printer(printer_ip, payload)
                
                margin_desc = "بدون حواف (0mm)" if margin_mm == 0 else f"هامش {margin_mm}mm"
                flash(f"تمت طباعة PDF بنجاح! [{margin_desc} | صفحات {[p+1 for p in selected_indices]}] 🖨️📄", "success")
            except Exception as e:
                flash(f"حدث خطأ أثناء طباعة PDF: {str(e)}", "danger")

    # معالجة الملفات الأخرى
    else:
        try:
            for _ in range(copies):
                os.startfile(file_path, "print")
            flash(f"تم إرسال الملف '{file.filename}' عبر نظام الطباعة بنجاح!", "success")
        except Exception as e:
            flash(f"تعذر طباعة هذا النوع من الملفات مباشرة: {str(e)}", "danger")

    return redirect(url_for('index'))

@app.route('/scan_network', methods=['POST'])
def scan_network():
    settings = load_settings()
    my_ip, subnet = get_local_ip_and_subnet()
    devices = scan_arp_devices(subnet, my_ip, settings["printer_ip"])
    app.cached_devices = devices
    flash(f"اكتمل فحص الشبكة! تم العثور على {len(devices)} جهاز نشط.", "info")
    return redirect(url_for('index'))

@app.route('/upload_file', methods=['POST'])
def upload_file():
    if 'shared_file' not in request.files:
        flash("لم يتم اختيار أي ملف!", "warning")
        return redirect(url_for('index'))

    file = request.files['shared_file']
    if file.filename == '':
        flash("يرجى اختيار ملف صالح!", "warning")
        return redirect(url_for('index'))

    dest_path = os.path.join(app.config['SHARED_FOLDER'], file.filename)
    file.save(dest_path)
    flash(f"تم رفع الملف '{file.filename}' بنجاح إلى المجلد المشترك!", "success")
    return redirect(url_for('index'))

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['SHARED_FOLDER'], filename, as_attachment=True)

@app.route('/delete_file/<filename>')
def delete_file(filename):
    file_path = os.path.join(app.config['SHARED_FOLDER'], filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        flash(f"تم حذف الملف '{filename}' من السيرفر.", "secondary")
    return redirect(url_for('index'))

if __name__ == '__main__':
    local_ip, subnet = get_local_ip_and_subnet()
    print("\n" + "="*60)
    print("🏠 سيرفر البيت الذكي (Smart Home Server) مع دعم الطباعة بدون حواف (0mm)!")
    print(f"👉 افتح هذا الرابط من متصفح أي موبايل أو كمبيوتر بالمنزل:")
    print(f"       http://{local_ip}:5000")
    print(f"   أو محلياً من هذا اللابتوب:")
    print(f"       http://localhost:5000")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=False)
