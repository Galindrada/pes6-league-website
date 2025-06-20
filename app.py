import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from flask_mysqldb import MySQL
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import MySQLdb.cursors
import pandas as pd

from config import Config

app = Flask(__name__)
app.config.from_object(Config)

mysql = MySQL(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Ensure the upload folder exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Define DOWNLOAD_FOLDER for generated CSVs
DOWNLOAD_FOLDER = os.path.join(app.root_path, 'static', 'downloads')
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# User model for Flask-Login
class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email

    @staticmethod
    def get(user_id):
        cur = mysql.connection.cursor()
        cur.execute("SELECT id, username, email FROM users WHERE id = %s", (user_id,))
        user_data = cur.fetchone()
        cur.close()
        if user_data:
            return User(user_data[0], user_data[1], user_data[2])
        return None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# --- Helper function for file uploads ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_media_type(filename):
    ext = filename.rsplit('.', 1)[1].lower()
    if ext in {'png', 'jpg', 'jpeg', 'gif', 'webp'}:
        return 'image'
    elif ext in {'mp4', 'avi', 'mov'}:
        return 'video'
    return 'none'

# --- Jinja2 Filter for Currency Formatting ---
@app.template_filter('format_currency')
def format_currency_filter(value):
    if isinstance(value, (int, float)):
        return f"€{value:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return value
# --- END Jinja2 Filter ---

# --- Routes ---

@app.route('/')
def index():
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT p.id, p.title, p.content, u.username, p.created_at, p.media_type, p.media_path
        FROM posts p
        JOIN users u ON p.user_id = u.id
        ORDER BY p.created_at DESC LIMIT 5
    """)
    recent_posts = cur.fetchall()
    cur.close()
    return render_template('index.html', recent_posts=recent_posts)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        cur = mysql.connection.cursor()
        try:
            cur.execute("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
                        (username, email, hashed_password))
            mysql.connection.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            mysql.connection.rollback()
            flash(f'Registration failed: {e}', 'danger')
        finally:
            cur.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT id, username, password, email FROM users WHERE username = %s", (username,))
        user_data = cur.fetchone()
        cur.close()

        if user_data and check_password_hash(user_data[2], password):
            user = User(user_data[0], user_data[1], user_data[3])
            login_user(user)
            flash('Logged in successfully.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@app.route('/blog')
def blog():
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT p.id, p.title, p.content, u.username, p.created_at, p.media_type, p.media_path
        FROM posts p
        JOIN users u ON p.user_id = u.id
        ORDER BY p.created_at DESC
    """)
    posts = cur.fetchall()
    cur.close()
    return render_template('blog.html', posts=posts)

@app.route('/blog/create', methods=['GET', 'POST'])
@login_required
def create_post():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        user_id = current_user.id
        media_type = 'none'
        media_path = None

        if not title or not content:
            flash('Title and content are required.', 'danger')
            return redirect(url_for('create_post'))

        if 'media_file' in request.files:
            file = request.files['media_file']
            if file.filename == '':
                flash('No selected file', 'warning')
            elif file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                full_upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(full_upload_path)
                media_type = get_media_type(filename)
                media_path = filename

        cur = mysql.connection.cursor()
        try:
            cur.execute("INSERT INTO posts (user_id, title, content, media_type, media_path) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, title, content, media_type, media_path))
            mysql.connection.commit()
            flash('Post created successfully!', 'success')
            return redirect(url_for('blog'))
        except Exception as e:
            mysql.connection.rollback()
            flash(f'Error creating post: {e}', 'danger')
        finally:
            cur.close()
    return render_template('create_post.html')

@app.route('/blog/post/<int:post_id>', methods=['GET', 'POST'])
def view_post(post_id):
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT p.id, p.title, p.content, u.username, p.created_at, p.media_type, p.media_path
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = %s
    """, (post_id,))
    post = cur.fetchone()

    if not post:
        flash('Post not found.', 'danger')
        return redirect(url_for('blog'))

    cur.execute("""
        SELECT c.content, u.username, c.created_at
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.post_id = %s
        ORDER BY c.created_at ASC
    """, (post_id,))
    comments = cur.fetchall()
    cur.close()

    if request.method == 'POST' and current_user.is_authenticated:
        comment_content = request.form['comment_content']
        user_id = current_user.id

        if not comment_content:
            flash('Comment cannot be empty.', 'danger')
            return redirect(url_for('view_post', post_id=post_id))

        cur = mysql.connection.cursor()
        try:
            cur.execute("INSERT INTO comments (post_id, user_id, content) VALUES (%s, %s, %s)",
                        (post_id, user_id, comment_content))
            mysql.connection.commit()
            flash('Comment added successfully!', 'success')
            return redirect(url_for('view_post', post_id=post_id))
        except Exception as e:
            mysql.connection.rollback()
            flash(f'Error adding comment: {e}', 'danger')
        finally:
            cur.close()
    return render_template('view_post.html', post=post, comments=comments)


@app.route('/team_management')
@login_required
def team_management():
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, team_name FROM league_teams WHERE user_id = %s", (current_user.id,))
    user_team = cur.fetchone()
    cur.close()

    team_players = []
    available_players = []

    if user_team:
        team_id = user_team[0]
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT p.id, p.player_name, p.registered_position
            FROM players p
            JOIN team_players tp ON p.id = tp.player_id
            WHERE tp.team_id = %s
            ORDER BY p.player_name ASC
        """, (team_id,))
        team_players = cur.fetchall()
        cur.close()
    # No "available_players" query here, as per design for a cleaner "My Team" page

    return render_template('team_management.html', user_team=user_team, team_players=team_players, available_players=available_players)

@app.route('/team_management/create', methods=['POST'])
@login_required
def create_team():
    team_name = request.form['team_name']
    user_id = current_user.id

    cur = mysql.connection.cursor()
    try:
        cur.execute("INSERT INTO league_teams (user_id, team_name) VALUES (%s, %s)",
                    (user_id, team_name))
        mysql.connection.commit()
        flash(f'Team "{team_name}" created successfully!', 'success')
    except Exception as e:
        mysql.connection.rollback()
        flash(f'Error creating team: {e}', 'danger')
    finally:
        cur.close()
    return redirect(url_for('team_management'))

@app.route('/team_management/add_player', methods=['POST'])
@login_required
def add_player_to_team():
    player_id = request.form['player_id']
    user_id = current_user.id

    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM league_teams WHERE user_id = %s", (current_user.id,))
    team_id_data = cur.fetchone()
    cur.close()

    if not team_id_data:
        flash('You must create a team first!', 'danger')
        return redirect(url_for('team_management'))

    team_id = team_id_data[0]

    cur = mysql.connection.cursor()
    try:
        cur.execute("INSERT INTO team_players (team_id, player_id) VALUES (%s, %s)",
                    (team_id, player_id))
        mysql.connection.commit()
        flash('Player added to your team!', 'success')
    except Exception as e:
            mysql.connection.rollback()
            flash(f'Error adding player: {e}', 'danger')
    finally:
            cur.close()
    return redirect(url_for('team_management'))

@app.route('/team_management/remove_player/<int:player_id>', methods=['POST'])
@login_required
def remove_player_from_team(player_id):
    user_id = current_user.id

    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM league_teams WHERE user_id = %s", (current_user.id,))
    team_id_data = cur.fetchone()
    cur.close()

    if not team_id_data:
        flash('You do not have a team.', 'danger')
        return redirect(url_for('team_management'))

    team_id = team_id_data[0]

    cur = mysql.connection.cursor()
    try:
        cur.execute("DELETE FROM team_players WHERE team_id = %s AND player_id = %s",
                    (team_id, player_id))
        mysql.connection.commit()
        flash('Player removed from your team!', 'success')
    except Exception as e:
            mysql.connection.rollback()
            flash(f'Error removing player: {e}', 'danger')
    finally:
            cur.close()
    return redirect(url_for('team_management'))

# --- New Routes for PES6 Game Data ---

@app.route('/pes6_game_teams')
def pes6_game_teams():
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, club_name FROM teams ORDER BY club_name ASC")
    game_teams = cur.fetchall()
    cur.close()
    return render_template('pes6_teams.html', game_teams=game_teams)

@app.route('/pes6_game_teams/<int:team_id>')
def pes6_team_details(team_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT club_name FROM teams WHERE id = %s", (team_id,))
    team_name = cur.fetchone()
    if not team_name:
        flash("PES6 Team not found!", "danger")
        return redirect(url_for('pes6_game_teams'))
    team_name = team_name[0]

    cur.execute("""
        SELECT id, player_name, registered_position, age, height, strong_foot, attack
        FROM players
        WHERE club_id = %s
        ORDER BY player_name ASC
    """, (team_id,))
    players_in_team = cur.fetchall()
    cur.close()
    return render_template('pes6_team_details.html', team_name=team_name, players_in_team=players_in_team)


@app.route('/pes6_player/<int:player_id>')
def pes6_player_details(player_id):
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT * FROM players WHERE id = %s", (player_id,))
    player_data = cur.fetchone()
    cur.close()

    if not player_data:
        flash("PES6 Player not found!", "danger")
        return redirect(url_for('pes6_game_teams'))

    basic_info = {
        'Name': player_data.get('player_name'),
        'Age': player_data.get('age'),
        'Height': player_data.get('height'),
        'Weight': player_data.get('weight'),
        'Nationality': player_data.get('nationality'),
        'Strong Foot': player_data.get('strong_foot'),
        'Favoured Side': player_data.get('favoured_side'),
        'Registered Position': player_data.get('registered_position'),
        'Games': 0,    # Added empty field
        'Assists': 0,  # Added empty field
        'Goals': 0     # Added empty field
    }

    club_name = None
    if player_data.get('club_id'):
        temp_cur = mysql.connection.cursor()
        temp_cur.execute("SELECT club_name FROM teams WHERE id = %s", (player_data['club_id'],))
        club_name_result = temp_cur.fetchone()
        if club_name_result:
            club_name = club_name_result[0]
        temp_cur.close()
    basic_info['Club'] = club_name

    financial_info = {
        'Salary': player_data.get('salary'),
        'Contract Years': player_data.get('contract_years_remaining'),
        'Market Value': player_data.get('market_value'),
        'Yearly Wage Rise': player_data.get('yearly_wage_rise')
    }

    skills_numeric = {
        'Attack': player_data.get('attack'),
        'Defense': player_data.get('defense'),
        'Balance': player_data.get('balance'),
        'Stamina': player_data.get('stamina'),
        'Top Speed': player_data.get('top_speed'),
        'Acceleration': player_data.get('acceleration'),
        'Response': player_data.get('response'),
        'Agility': player_data.get('agility'),
        'Dribble Accuracy': player_data.get('dribble_accuracy'),
        'Dribble Speed': player_data.get('dribble_speed'),
        'Short Pass Accuracy': player_data.get('short_pass_accuracy'),
        'Short Pass Speed': player_data.get('short_pass_speed'),
        'Long Pass Accuracy': player_data.get('long_pass_accuracy'),
        'Long Pass Speed': player_data.get('long_pass_speed'),
        'Shot Accuracy': player_data.get('shot_accuracy'),
        'Shot Power': player_data.get('shot_power'),
        'Shot Technique': player_data.get('shot_technique'),
        'Free Kick Accuracy': player_data.get('free_kick_accuracy'),
        'Swerve': player_data.get('swerve'),
        'Heading': player_data.get('heading'),
        'Jump': player_data.get('jump'),
        'Technique': player_data.get('technique'),
        'Aggression': player_data.get('aggression'),
        'Mentality': player_data.get('mentality'),
        'Goal Keeping': player_data.get('goal_keeping'),
        'Team Work': player_data.get('team_work'),
        'Consistency': player_data.get('consistency'),
        'Condition / Fitness': player_data.get('condition_fitness'),
    }

    positional_skills = {
        'GK': player_data.get('gk'), 'CWP': player_data.get('cwp'), 'CBT': player_data.get('cbt'),
        'SB': player_data.get('sb'), 'DMF': player_data.get('dmf'), 'WB': player_data.get('wb'),
        'CMF': player_data.get('cmf'), 'SMF': player_data.get('smf'), 'AMF': player_data.get('amf'),
        'WF': player_data.get('wf'), 'SS': player_data.get('ss'), 'CF': player_data.get('cf')
    }

    special_skills = {
        'Dribbling': player_data.get('dribbling_skill'),
        'Tactical Dribble': player_data.get('tactical_dribble'),
        'Positioning': player_data.get('positioning'),
        'Reaction': player_data.get('reaction'),
        'Playmaking': player_data.get('playmaking'),
        'Passing': player_data.get('passing'),
        'Scoring': player_data.get('scoring'),
        '1-on-1 Scoring': player_data.get('one_one_scoring'),
        'Post Player': player_data.get('post_player'),
        'Lines': player_data.get('lines'),
        'Middle Shooting': player_data.get('middle_shooting'),
        'Side': player_data.get('side'),
        'Centre': player_data.get('centre'),
        'Penalties': player_data.get('penalties'),
        '1-Touch Pass': player_data.get('one_touch_pass'),
        'Outside': player_data.get('outside'),
        'Marking': player_data.get('marking'),
        'Sliding': player_data.get('sliding'),
        'Covering': player_data.get('covering'),
        'D-Line Control': player_data.get('d_line_control'),
        'Penalty Stopper': player_data.get('penalty_stopper'),
        '1-on-1 Stopper': player_data.get('one_on_one_stopper'),
        'Long Throw': player_data.get('long_throw'),
    }


    return render_template('pes6_player_details.html',
                           player=player_data,
                           basic_info=basic_info,
                           financial_info=financial_info, # Pass new financial_info dictionary
                           skills_numeric=skills_numeric,
                           positional_skills=positional_skills,
                           special_skills=special_skills)

# --- NEW ROUTES FOR TOOLS PAGE AND CSV DOWNLOAD ---
@app.route('/tools')
def tools():
    return render_template('tools.html')

@app.route('/download_updated_csv')
@login_required # Often good to require login for tools/downloads
def download_updated_csv():
    try:
        cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        # Fetch all player data, including financial info
        cur.execute("""
            SELECT
                p.id, p.player_name, p.shirt_name, t.club_name AS club_team_raw,
                p.registered_position, p.age, p.height, p.weight, p.nationality,
                p.strong_foot, p.favoured_side, p.gk, p.cwp, p.cbt, p.sb, p.dmf, p.wb,
                p.cmf, p.smf, p.amf, p.wf, p.ss, p.cf, p.attack, p.defense, p.balance,
                p.stamina, p.top_speed, p.acceleration, p.response, p.agility,
                p.dribble_accuracy, p.dribble_speed, p.short_pass_accuracy,
                p.short_pass_speed, p.long_pass_accuracy, p.long_pass_speed,
                p.shot_accuracy, p.shot_power, p.shot_technique,
                p.free_kick_accuracy, p.swerve, p.heading, p.jump, p.technique,
                p.aggression, p.mentality, p.goal_keeping, p.team_work, p.consistency,
                p.condition_fitness, p.dribbling_skill, p.tactical_dribble, p.positioning,
                p.reaction, p.playmaking, p.passing, p.scoring, p.one_one_scoring,
                p.post_player, p.lines, p.middle_shooting, p.side, p.centre, p.penalties,
                p.one_touch_pass, p.outside, p.marking, p.sliding, p.covering,
                p.d_line_control, p.penalty_stopper, p.one_on_one_stopper, p.long_throw,
                p.injury_tolerance, p.dribble_style, p.free_kick_style, p.pk_style,
                p.drop_kick_style, p.skin_color, p.face_type, p.preset_face_number,
                p.head_width, p.neck_length, p.neck_width, p.shoulder_height,
                p.shoulder_width, p.chest_measurement, p.waist_circumference,
                p.arm_circumference, p.leg_circumference, p.calf_circumference,
                p.leg_length, p.wristband, p.wristband_color, p.international_number,
                p.classic_number, p.club_number,
                p.salary, p.contract_years_remaining, p.market_value, p.yearly_wage_rise
            FROM players p
            LEFT JOIN teams t ON p.club_id = t.id
            ORDER BY p.id
        """)
        players_data = cur.fetchall()
        cur.close()

        if not players_data:
            flash("No player data available to export.", "warning")
            return redirect(url_for('tools'))

        # Define the desired column order and original CSV headers
        csv_header_map = {
            'id': 'ID',
            'player_name': 'NAME',
            'shirt_name': 'SHIRT_NAME',
            'gk': 'GK  0',
            'cwp': 'CWP  2',
            'cbt': 'CBT  3',
            'sb': 'SB  4',
            'dmf': 'DMF  5',
            'wb': 'WB  6',
            'cmf': 'CMF  7',
            'smf': 'SMF  8',
            'amf': 'AMF  9',
            'wf': 'WF 10',
            'ss': 'SS  11',
            'cf': 'CF  12',
            'registered_position': 'REGISTERED POSITION',
            'height': 'HEIGHT',
            'strong_foot': 'STRONG FOOT',
            'favoured_side': 'FAVOURED SIDE',
            'weak_foot_accuracy': 'WEAK FOOT ACCURACY', # Note: Removed from DB, will be 0/empty
            'weak_foot_frequency': 'WEAK FOOT FREQUENCY', # Note: Removed from DB, will be 0/empty
            'attack': 'ATTACK', 'defense': 'DEFENSE', 'balance': 'BALANCE',
            'stamina': 'STAMINA', 'top_speed': 'TOP SPEED', 'acceleration': 'ACCELERATION',
            'response': 'RESPONSE', 'agility': 'AGILITY', 'dribble_accuracy': 'DRIBBLE ACCURACY',
            'dribble_speed': 'DRIBBLE SPEED', 'short_pass_accuracy': 'SHORT PASS ACCURACY',
            'short_pass_speed': 'SHORT PASS SPEED', 'long_pass_accuracy': 'LONG PASS ACCURACY',
            'long_pass_speed': 'LONG PASS SPEED', 'shot_accuracy': 'SHOT ACCURACY',
            'shot_power': 'SHOT POWER', 'shot_technique': 'SHOT TECHNIQUE',
            'free_kick_accuracy': 'FREE KICK ACCURACY', 'swerve': 'SWERVE', 'heading': 'HEADING',
            'jump': 'JUMP', 'technique': 'TECHNIQUE', 'aggression': 'AGGRESSION',
            'mentality': 'MENTALITY', 'goal_keeping': 'GOAL KEEPING', 'team_work': 'TEAM WORK',
            'consistency': 'CONSISTENCY', 'condition_fitness': 'CONDITION / FITNESS',
            'dribbling_skill': 'DRIBBLING', 'tactical_dribble': 'TACTIAL DRIBBLE',
            'positioning': 'POSITIONING', 'reaction': 'REACTION', 'playmaking': 'PLAYMAKING',
            'passing': 'PASSING', 'scoring': 'SCORING', 'one_one_scoring': '1-1 SCORING',
            'post_player': 'POST PLAYER', 'lines': 'LINES', 'middle_shooting': 'MIDDLE SHOOTING',
            'side': 'SIDE', 'centre': 'CENTRE', 'penalties': 'PENALTIES',
            'one_touch_pass': '1-TOUCH PASS', 'outside': 'OUTSIDE', 'marking': 'MARKING',
            'sliding': 'SLIDING', 'covering': 'COVERING', 'd_line_control': 'D-LINE CONTROL',
            'penalty_stopper': 'PENALTY STOPPER', 'one_on_one_stopper': '1-ON-1 STOPPER',
            'long_throw': 'LONG THROW', 'injury_tolerance': 'INJURY TOLERANCE',
            'dribble_style': 'DRIBBLE STYLE', 'free_kick_style': 'FREE KICK STYLE',
            'pk_style': 'PK STYLE', 'drop_kick_style': 'DROP KICK STYLE',
            'age': 'AGE', 'weight': 'WEIGHT', 'nationality': 'NATIONALITY',
            'skin_color': 'SKIN COLOR', 'face_type': 'FACE TYPE', 'preset_face_number': 'PRESET FACE NUMBER',
            'head_width': 'HEAD WIDTH', 'neck_length': 'NECK LENGTH', 'neck_width': 'NECK WIDTH',
            'shoulder_height': 'SHOULDER HEIGHT', 'shoulder_width': 'SHOULDER WIDTH',
            'chest_measurement': 'CHEST MEASUREMENT', 'waist_circumference': 'WAIST CIRCUMFERENCE',
            'arm_circumference': 'ARM CIRCUMFERENCE', 'leg_circumference': 'LEG CIRCUMFERENCE',
            'calf_circumference': 'CALF CIRCUMFERENCE', 'leg_length': 'LEG LENGTH',
            'wristband': 'WRISTBAND', 'wristband_color': 'WRISTBAND COLOR',
            'international_number': 'INTERNATIONAL NUMBER', 'classic_number': 'CLASSIC NUMBER',
            'club_number': 'CLUB NUMBER',
            'club_team_raw': 'CLUB TEAM', # Ensure this maps back to original 'CLUB TEAM'
            # New financial fields
            'salary': 'SALARY',
            'contract_years_remaining': 'CONTRACT YEARS REMAINING',
            'market_value': 'MARKET VALUE',
            'yearly_wage_rise': 'YEARLY WAGE RISE'
        }

        df_players = pd.DataFrame(players_data)

        current_to_desired_map = {sql_col: csv_header_map[sql_col] for sql_col in csv_header_map if sql_col in df_players.columns}
        df_players_output = df_players.rename(columns=current_to_desired_map)

        # Correct the list of original CSV headers provided in a previous turn (100 headers)
        original_csv_headers_exact = [
            'ID', 'NAME', 'SHIRT_NAME', 'GK  0', 'CWP  2', 'CBT  3', 'SB  4', 'DMF  5', 'WB  6', 'CMF  7', 'SMF  8', 'AMF  9', 'WF 10', 'SS  11', 'CF  12', 'REGISTERED POSITION', 'HEIGHT', 'STRONG FOOT', 'FAVOURED SIDE', 'WEAK FOOT ACCURACY', 'WEAK FOOT FREQUENCY', 'ATTACK', 'DEFENSE', 'BALANCE', 'STAMINA', 'TOP SPEED', 'ACCELERATION', 'RESPONSE', 'AGILITY', 'DRIBBLE ACCURACY', 'DRIBBLE SPEED', 'SHORT PASS ACCURACY', 'SHORT PASS SPEED', 'LONG PASS ACCURACY', 'LONG PASS SPEED', 'SHOT ACCURACY', 'SHOT POWER', 'SHOT TECHNIQUE', 'FREE KICK ACCURACY', 'SWERVE', 'HEADING', 'JUMP', 'TECHNIQUE', 'AGGRESSION', 'MENTALITY', 'GOAL KEEPING', 'TEAM WORK', 'CONSISTENCY', 'CONDITION / FITNESS', 'DRIBBLING', 'TACTIAL DRIBBLE', 'POSITIONING', 'REACTION', 'PLAYMAKING', 'PASSING', 'SCORING', '1-1 SCORING', 'POST PLAYER', 'LINES', 'MIDDLE SHOOTING', 'SIDE', 'CENTRE', 'PENALTIES', '1-TOUCH PASS', 'OUTSIDE', 'MARKING', 'SLIDING', 'COVERING', 'D-LINE CONTROL', 'PENALTY STOPPER', '1-ON-1 STOPPER', 'LONG THROW', 'INJURY TOLERANCE', 'DRIBBLE STYLE', 'FREE KICK STYLE', 'PK STYLE', 'DROP KICK STYLE', 'AGE', 'WEIGHT', 'NATIONALITY', 'SKIN COLOR', 'FACE TYPE', 'PRESET FACE NUMBER', 'HEAD WIDTH', 'NECK LENGTH', 'NECK WIDTH', 'SHOULDER HEIGHT', 'SHOULDER WIDTH', 'CHEST MEASUREMENT', 'WAIST CIRCUMFERENCE', 'ARM CIRCUMFERENCE', 'LEG CIRCUMFERENCE', 'CALF CIRCUMFERENCE', 'LEG LENGTH', 'WRISTBAND', 'WRISTBAND COLOR', 'INTERNATIONAL NUMBER', 'CLASSIC NUMBER', 'CLUB TEAM', 'CLUB NUMBER'
        ]
        
        # Append new financial headers to the exact original list
        financial_headers = ['SALARY', 'CONTRACT YEARS REMAINING', 'MARKET VALUE', 'YEARLY WAGE RISE']
        final_column_order = original_csv_headers_exact + financial_headers

        df_players_output = df_players_output.reindex(columns=final_column_order)

        for col in df_players_output.columns:
            if df_players_output[col].dtype == 'object':
                df_players_output[col] = df_players_output[col].fillna('')
            else:
                df_players_output[col] = df_players_output[col].fillna(0)

        output_filename = 'pe6_player_data_updated.csv'
        output_filepath = os.path.join(DOWNLOAD_FOLDER, output_filename)

        df_players_output.to_csv(output_filepath, index=False, encoding='utf-8')

        return send_from_directory(DOWNLOAD_FOLDER, output_filename, as_attachment=True)

    except Exception as e:
        flash(f"Error generating or downloading CSV: {e}", "danger")
        app.logger.error(f"Error in download_updated_csv: {e}", exc_info=True)
        return redirect(url_for('tools'))

if __name__ == '__main__':
    # For local development
    # app.run(debug=True)

    # For production with Waitress (if you decide to use it manually)
    from waitress import serve
    serve(app, host='0.0.0.0', port=5000)
