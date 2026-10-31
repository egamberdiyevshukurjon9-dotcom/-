# -*- coding: utf-8 -*-
"""Флешкага ёзишга тайёр, ҳеч нарса ўрнатишни талаб қилмайдиган пакетни йиғади.

Натижа:  dist/VoicePresentation-Flash/   ва   dist/VoicePresentation-Flash.zip
Ичида:   START.bat, TEST.bat, ...   VoiceApp/runtime (Windows учун Python),
         VoiceApp/app (дастур), VoiceApp/models (Vosk моделлари)

Ишлатиш (Windows, Linux ёки macOS'да, интернет керак):
    python tools/build_flash.py
    python tools/build_flash.py --no-models        # моделларсиз (тез, синов учун)
    python tools/build_flash.py --models uz,en

Пакет Windows 10/11 (64-бит) учун; йиғиш компьютери ихтиёрий — кутубхоналар
`pip download --platform win_amd64` орқали Windows версияси юкланади.
"""
import argparse
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSIONS = ["3.12.10", "3.12.9", "3.12.8"]   # биринчиси топилса — шу
PYTHON_TAG = "312"
EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"

MODELS = {
    "uz": "vosk-model-small-uz-0.22",
    "ru": "vosk-model-small-ru-0.22",
    "en": "vosk-model-small-en-us-0.15",
}
MODEL_URL = "https://alphacephei.com/vosk/models/{name}.zip"

# vosk 0.3.45 `srt`, `requests`, `tqdm` ни import қилади (транскрибер/модель юклаш учун)
VOSK_REQUIREMENT = "vosk==0.3.45"
REQUIREMENTS = ["cffi", "requests", "tqdm", "colorama", "websockets",
                "sounddevice", "pywin32", "SpeechRecognition==3.11.0"]

ROOT = Path(__file__).resolve().parent.parent      # voice-presentation/
USER_AGENT = "voice-presentation-build/1.0"


def log(message: str) -> None:
    print("[build] " + message, flush=True)


# ---------------------------------------------------------------------------
def download(url: str, target: Path, attempts: int = 4) -> Path:
    if target.exists() and target.stat().st_size > 0:
        log("кэшдан: %s" % target.name)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        try:
            log("юкланмоқда: %s" % url)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
            if target.stat().st_size == 0:
                raise OSError("бўш файл")
            return target
        except Exception as error:
            target.unlink(missing_ok=True)
            if attempt == attempts:
                raise
            log("хато (%s), %d сониядан кейин қайта уринилади" % (error, 3 * attempt))
            time.sleep(3 * attempt)
    raise AssertionError("unreachable")


def run(command, **kwargs) -> None:
    log("$ " + " ".join(str(c) for c in command))
    subprocess.run([str(c) for c in command], check=True, **kwargs)


# ---------------------------------------------------------------------------
def install_runtime(cache: Path, runtime: Path, embed_zip: str = "") -> str:
    version, last_error = "local", None
    if embed_zip:  # олдиндан юкланган архив (интернетсиз/синов йиғиш учун)
        archive = Path(embed_zip)
    else:
        for version in PYTHON_VERSIONS:
            try:
                archive = download(EMBED_URL.format(v=version),
                                   cache / ("python-%s-embed-amd64.zip" % version))
                break
            except Exception as error:
                last_error = error
        else:
            raise SystemExit("Embeddable Python юкланмади: %s" % last_error)
    if not zipfile.is_zipfile(archive):
        raise SystemExit("Юкланган Python архиви бузуқ: %s" % archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(runtime)
    # site-packages ва дастур папкаси import йўлида бўлиши, `import site` ёқилиши керак
    (runtime / ("python%s._pth" % PYTHON_TAG)).write_text(
        "python%s.zip\n.\nLib\\site-packages\n..\\app\nimport site\n" % PYTHON_TAG, encoding="ascii")
    (runtime / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    return version


def extract_wheel(wheel: Path, site_packages: Path) -> None:
    with zipfile.ZipFile(wheel) as z:
        for info in z.infolist():
            name = info.filename
            if name.split("/", 1)[0].endswith(".data"):
                parts = name.split("/", 2)
                if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                    continue          # scripts, headers, data — керак эмас
                name = parts[2]
            if not name or name.endswith("/"):
                continue
            destination = site_packages / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)


def pip_download(destination: Path, requirements, *extra) -> None:
    run([sys.executable, "-m", "pip", "download", "--quiet", "--dest", destination,
         "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.12",
         "--implementation", "cp", "--abi", "cp" + PYTHON_TAG, *extra, *requirements])


def install_packages(cache: Path, site_packages: Path) -> None:
    wheels = cache / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    pip_download(wheels, [VOSK_REQUIREMENT], "--no-deps")
    pip_download(wheels, REQUIREMENTS)
    for wheel in sorted(wheels.glob("*.whl")):
        extract_wheel(wheel, site_packages)
    log("%d та кутубхона ўрнатилди" % len(list(wheels.glob("*.whl"))))

    # `srt` фақат манба (sdist) кўринишида бор — битта pure-python файл
    sdist_dir = cache / "sdist"
    sdist_dir.mkdir(exist_ok=True)
    if not list(sdist_dir.glob("srt-*.tar.gz")):
        run([sys.executable, "-m", "pip", "download", "--quiet", "--no-deps", "--no-binary=:all:",
             "--dest", sdist_dir, "srt"])
    archive = next(sdist_dir.glob("srt-*.tar.gz"))
    with tarfile.open(archive) as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("/srt.py"))
        data = tar.extractfile(member).read()
    (site_packages / "srt.py").write_bytes(data)

    # керакмас оғир файллар: инглизча Sphinx маълумоти (28 МБ), Linux/macOS flac, ёрдам файли
    sr = site_packages / "speech_recognition"
    shutil.rmtree(sr / "pocketsphinx-data", ignore_errors=True)
    for name in ("flac-linux-x86", "flac-linux-x86_64", "flac-mac"):
        (sr / name).unlink(missing_ok=True)
    (site_packages / "PyWin32.chm").unlink(missing_ok=True)
    shutil.rmtree(site_packages / "tests", ignore_errors=True)   # SpeechRecognition 3.11 тасодифан киритган
    if not (sr / "flac-win32.exe").exists():
        raise SystemExit("speech_recognition/flac-win32.exe топилмади — Google режими ишламайди")


def install_models(cache: Path, models_dir: Path, languages) -> None:
    for language in languages:
        name = MODELS[language]
        archive = download(MODEL_URL.format(name=name), cache / (name + ".zip"))
        target = models_dir / language
        if target.exists():
            shutil.rmtree(target)
        with zipfile.ZipFile(archive) as z:
            top = {n.split("/", 1)[0] for n in z.namelist() if n.strip("/")}
            z.extractall(models_dir)
        if len(top) != 1:
            raise SystemExit("Модель архиви кутилмаган тузилишда: %s" % top)
        (models_dir / top.pop()).rename(target)
        if not ((target / "am" / "final.mdl").exists() or (target / "conf" / "model.conf").exists()):
            raise SystemExit("«%s» модели тўлиқ эмас: %s" % (language, target))
        log("модель «%s» тайёр (%s)" % (language, name))


def copy_app(app_dir: Path) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted((ROOT / "app").glob("*.py")):
        shutil.copy2(source, app_dir / source.name)


def copy_templates(output: Path) -> None:
    for source in (ROOT / "flash").rglob("*"):
        if source.is_file():
            target = output / source.relative_to(ROOT / "flash")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def git_revision() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return "?"


def make_zip(folder: Path, archive: Path) -> str:
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(folder.parent).as_posix())
    digest = hashlib.sha256()
    with open(archive, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "dist"), help="натижа папкаси")
    parser.add_argument("--cache", default=str(ROOT / "build" / "cache"), help="юкламалар кэши")
    parser.add_argument("--models", default=",".join(MODELS), help="масалан uz,ru,en")
    parser.add_argument("--no-models", action="store_true", help="моделларни қўшмаслик")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--embed-zip", default="", help="тайёр python-3.12.x-embed-amd64.zip йўли")
    args = parser.parse_args()

    languages = [] if args.no_models else [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in languages if m not in MODELS]
    if unknown:
        raise SystemExit("Номаълум модель: %s (мумкин: %s)" % (", ".join(unknown), ", ".join(MODELS)))

    out_root, cache = Path(args.out).resolve(), Path(args.cache).resolve()
    package = out_root / "VoicePresentation-Flash"
    if package.exists():
        shutil.rmtree(package)
    voice_app = package / "VoiceApp"
    voice_app.mkdir(parents=True)

    python_version = install_runtime(cache, voice_app / "runtime", args.embed_zip)
    install_packages(cache, voice_app / "runtime" / "Lib" / "site-packages")
    copy_templates(package)
    copy_app(voice_app / "app")
    (voice_app / "models").mkdir()
    install_models(cache, voice_app / "models", languages)

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (voice_app / "VERSION.txt").write_text(
        "Build: %s\nGit: %s\nPython: %s (embeddable, win-amd64)\nModels: %s\n"
        % (stamp, git_revision(), python_version, ", ".join(languages) or "-"), encoding="utf-8")

    size = sum(p.stat().st_size for p in package.rglob("*") if p.is_file())
    log("пакет тайёр: %s (%.0f МБ)" % (package, size / 1e6))
    if not args.no_zip:
        digest = make_zip(package, out_root / "VoicePresentation-Flash.zip")
        zip_size = (out_root / "VoicePresentation-Flash.zip").stat().st_size
        log("zip: %s (%.0f МБ), sha256=%s" % (out_root / "VoicePresentation-Flash.zip", zip_size / 1e6, digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
