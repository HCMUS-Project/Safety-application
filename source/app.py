import os
import installLib
from form.authenForm import LoginForm, RegisterForm
from form.changeInfoForm import ChangeInfoForm
from form.uploadFileForm import UploadFileForm
from Crypto.PublicKey import RSA
from werkzeug.utils import secure_filename
import authen
import cryptography
import changeInfo
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for, session
import json
from flask_bootstrap import Bootstrap
from pymongo import MongoClient
from Crypto.PublicKey import RSA

load_dotenv()  # take environment variables from .env.

UPLOAD_FOLDER = "uploads"
DOWNLOAD_FOLDER = "download"
SALT_LENGTH = int(os.getenv("SALT_LENGTH"))
SECRET_KEY = os.getenv("SECRET_KEY")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_PORT = os.getenv("MONGO_PORT")
APP_PORT = os.getenv("APP_PORT")

basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)


# Flask-WTF requires an encryption key - the string can be anything
app.config['SECRET_KEY'] = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DOWNLOAD_FOLDER'] = DOWNLOAD_FOLDER

# create "uploads" folder


def create_uploads_folder():
    os.path.exists(UPLOAD_FOLDER) or os.mkdir(UPLOAD_FOLDER)


def create_download_folder():
    os.path.exists(DOWNLOAD_FOLDER) or os.mkdir(DOWNLOAD_FOLDER)


# connect to mongoDB
try:
    client = MongoClient(MONGO_URI, int(MONGO_PORT))
    db = client.myDatabase  # database name
except:
    app.logger.error("Can not connect MongoDB")


Bootstrap(app)


def authorize():
    if not 'user' in session:
        return redirect(url_for('login'))

    user = json.loads(session["user"])

    return user


@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()

    if request.method == "POST":
        passwd = request.form.get("password")
        passphase = authen.salt_hash256(passwd)

        # check if email exists
        if (authen.check_email_exists(request.form.get("email"))):
            return render_template("register.html", form=form, error="Email exist.")

        public_key, private_key = cryptography.gen_user_RSA_key_pem(
            passphase)

        user = {
            "avatar": 'https://avatars.dicebear.com/api/human/{}.svg'.format(
                form.name.data),
            "email": form.email.data,
            "name": form.name.data,
            "phone": form.phone.data,
            "address": form.address.data,
            "passphase": passphase,
            "pass": passwd,  # temp
            "public_key": public_key,
            "private_key": private_key,
        }

        db.users.insert_one(user)

        return redirect(url_for('login'))
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if request.method == "POST":
        email = request.form.get("email")
        passwd = request.form.get("password")

        app.logger.info("Login..... email: %s - pass: %s", email, passwd)

        # check if email exists
        user = db.users.find_one({"email": email})

        if user is None:
            return render_template("login.html", form=form, error="Email does not exist.")

        if authen.verify_password(passwd, user["passphase"]):
            user = json.dumps(user, default=str)
            session["user"] = user
            return redirect(url_for('home'))
        else:
            return render_template("login.html", form=form, error="Password is incorrect.")

    return render_template('login.html', form=form)


@app.route("/logout")
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))


@ app.route('/home', methods=['GET', 'POST'])
def home():
    # authorize user
    user = authorize()

    form = ChangeInfoForm()
    upload_form = UploadFileForm()

    sharedFiles = db.shared_file.find({"email": user['email']})
    files = []
    for file in sharedFiles:
        files.append(file['name'])

    return render_template('home.html', form=form, upload_form=upload_form, user=user, files=files)


@app.route("/change-info", methods=['GET', 'POST'])
def change_info():
    user = authorize()
    print("user", user)

    if request.method == "POST":
        new_info = {
            "email": request.form.get("email"),
            "name": request.form.get("name"),
            "phone": request.form.get("phone"),
            "address": request.form.get("address"),
            "passphase": request.form.get("password")
        }

        app.logger.info("----- CHANGE INFO --------")
        app.logger.info("new info: %s", new_info)

        changeInfo.change_info(new_info, user)

    return redirect(url_for('home'))


@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    # check if email exist
    email = request.form.get("email")
    receiver = db.users.find_one({"email": email})
    uploaded_file = request.files['file']

    if receiver is None:
        return redirect(url_for('home', status="fail", content="This user does not exist!"))

    if not uploaded_file:
        return redirect(url_for('home', status="fail", content="Please select 1 file"))

    # read content file
    filename = secure_filename(uploaded_file.filename)
    content = uploaded_file.read()

    if len(content) >= 15728640:  # 15mb
        return redirect(url_for('home', status="fail", content="File too large (>15MB)"))

    # gen Ksession and encrypted it with RSA
    ksession, en_ksession = cryptography.gen_session_key(
        receiver["public_key"])

    cipher_text = cryptography.AES_encrypt(content, ksession)

    encrypted = b''.join([en_ksession, b'[+++++]', cipher_text])

    new_file = {
        "name": filename,
        "email": email,
        "content": encrypted
    }
    db.shared_file.insert_one(new_file)

    return redirect(url_for('home', status="success", content="File has been uploaded"))


@app.route('/decrypt', methods=['GET', 'POST'])
def decrypt_file():
    create_download_folder()
    user = authorize()
    #
    select = request.form.get('select')
    file = db.shared_file.find_one({"name": select})

    if file is None:
        return redirect(url_for('home', status="fail", content="Please select 1 file"))

    encrypted = file["content"].split(b'[+++++]')
    en_ksession, cipher_text = encrypted[0], encrypted[1]
    # get private key
    priv_key_pem = cryptography.AES_decrypt(
        user["private_key"], user["passphase"])

    ksession = cryptography.RSA_decrypt(en_ksession, priv_key_pem)
    content = cryptography.AES_decrypt(cipher_text.decode(), ksession)

    file_path = os.path.join(basedir, app.config['DOWNLOAD_FOLDER'], select)
    open(file_path, 'wb').write(content)

    return redirect(url_for('home', status="success", content="File has been decrypted"))


@app.route('/sign-on', methods=['GET', 'POST'])
def sign_on_file():
    create_uploads_folder()

    user = authorize()

    try:
        uploaded_file = request.files['file']

        filename = secure_filename(uploaded_file.filename)
        content = uploaded_file.read()

        # save
        uploaded_file.stream.seek(0)
        uploaded_file.save(os.path.join(
            basedir, app.config['UPLOAD_FOLDER'], filename))

        hashed_file = int(cryptography.sha256(content), 16)

        user["private_key"] = cryptography.AES_decrypt(
            user["private_key"], user["passphase"])

        private_key = RSA.importKey(user["private_key"])

        signature = pow(hashed_file, private_key.d, private_key.n)

        signed_file_path = os.path.join(
            basedir, app.config['UPLOAD_FOLDER'], filename + ".sig")

        open(signed_file_path, "w").write(str(signature))
    except:
        return redirect(url_for('home', status="fail", content="No file uploaded!"))

    return redirect(url_for('home', status="success", content="File is signed."))


@ app.route('/verify', methods=['GET', 'POST'])
def verify_signed_file():
    user = authorize()
    file = request.files['file']
    sign_file = request.files['sign_file']

    filename = secure_filename(file.filename)
    sign_filename = secure_filename(sign_file.filename)

    signature = int(sign_file.read().decode())

    hashed_file = int(cryptography.sha256(file.read()), 16)

    users = db.users.find()
    for user in users:
        public_key = RSA.importKey(user["public_key"])

        hashFromSignature = pow(signature,
                                public_key.e, public_key.n)
        if hashFromSignature == hashed_file:
            return redirect(url_for('home', status="success", content="File is verified."))

    if filename == "":
        return redirect(url_for('home', status="fail", content="No file uploaded!"))

    elif sign_filename == "":
        return redirect(url_for('home', status="fail", content="No file Signature uploaded!"))

    elif sign_filename.split(".")[-1] != "sig":
        return redirect(url_for('home', status="fail", content="Wrong file Signature format!"))
    return redirect(url_for('home', status="fail", content="Verify failed!"))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=APP_PORT, threaded=True, debug=True)
