# OER/accounts/views/views.py
from django.contrib import messages
from django.shortcuts import render, redirect
# Import Django's authentication tools
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required

from .contributor.expertise_service import save_user_expertise
from .contributor.generate_expertise import generate_expertise
from .contributor.recommendation_service import recommend_courses_for_contributor, \
    save_expertise_and_generate_course_links
from .email.email_service import RegistrationSuccessEmail
from ..models import User
from ..forms import ProfilePictureForm # Add this new import at the top
from .syllabus_upload import extract_and_upload
from django.shortcuts import render, redirect
from django.http import JsonResponse
from ..models import Program, Expertise, User  # Adjust import as per your project

from django.utils import timezone

# --- REAL LOGIN VIEW ---
# OER/accounts/views.py

def home_view(request):
    print("Home view called")
    # generate_expertise()
    # Clear all session data safely
    request.session.flush()  # deletes session data and session cookie
    return render(request, 'home/index.html')

def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password')

        user = authenticate(request, username=email, password=password)

        if user is not None:
            # If contributor is approved, is_active will be True (backend already blocks inactive)
            login(request, user)
            request.session['user_id'] = user.id
            request.session['role'] = user.role

            if user.role == 'CONTRIBUTOR':
                return redirect('contributor_dashboard')
            elif user.role == 'STUDENT':
                return redirect('student_dashboard')
            return redirect('home')

        # If auth failed, check if contributor exists but pending/rejected
        try:
            u = User.objects.get(email__iexact=email)
            if u.role == User.Role.CONTRIBUTOR:
                if u.contributor_approval_status == User.ContributorApprovalStatus.PENDING:
                    messages.info(request, "Your contributor account is pending approval.")
                    return redirect("pending_approval")
                if u.contributor_approval_status == User.ContributorApprovalStatus.REJECTED:
                    msg = "Your contributor account was rejected."
                    if u.contributor_rejection_reason:
                        msg += f" Reason: {u.contributor_rejection_reason}"
                    messages.error(request, msg)
                    return redirect("login")
        except User.DoesNotExist:
            pass

        messages.error(request, "Invalid credentials.")
        return redirect("/login/?tab=login")

    return render(request, 'home/register.html')



# Contributor Dashboard
@login_required
def contributor_dashboard_view(request):
    form = ProfilePictureForm(instance=request.user)
    return render(request, 'contributor/contributor_dashboard.html', {'form': form})

# Student Dashboard
@login_required
def student_dashboard(request):
    form = ProfilePictureForm(instance=request.user)
    courses = Course.objects.all()

    print("Courses count:", courses.count())

    return render(request, 'student/student_dashboard.html', {
        'form': form,
        'courses': courses
    })

# --- LOGOUT VIEW ---
def logout_view(request):
    logout(request)
    return redirect('login') # Redirect to login page after logout

def register_view(request):
    # ---- Handle AJAX request for expertise dropdown ----
    # if request.method == 'GET' and request.GET.get('program'):
    #     program_name = request.GET.get('program')
    #     try:
    #         program = Program.objects.get(program_name=program_name)
    #
    #     except Program.DoesNotExist:
    #         return JsonResponse([], safe=False)

    # ---- Handle form submission ----
    if request.method == 'POST':
        role = request.POST.get('role', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()

        # Validation
        if not email or not password:
            print("ERROR: Email or password was empty.")
            return redirect('register')

        if User.objects.filter(email__iexact=email).exists():
            print(f"ERROR: User with this email already exists.")
            return redirect('register')

        # Create user
        user = User.objects.create_user(username=email, email=email)
        user.set_password(password)

        # Role-specific data
        if role == 'student':
            user.role = User.Role.STUDENT
            user.first_name = request.POST.get('student-name', '').strip()
            user.college_name = request.POST.get('college-name', '').strip()
            user.date_of_birth = request.POST.get('dob') or None
            user.gender = request.POST.get('gender', '').strip()
            user.course = request.POST.get('course', '').strip()
            user.year = request.POST.get('year', '').strip()
            messages.success(request, "Registration successful. You can log in now.")
            return redirect("login")

        elif role == "contributor":
            user.role = User.Role.CONTRIBUTOR
            user.first_name = request.POST.get("contrib-fname", "").strip()
            user.last_name = request.POST.get("contrib-lname", "").strip()
            user.phone_number = request.POST.get("contrib-phone", "").strip()
            user.designation = request.POST.get("designation", "").strip()
            user.current_institution = request.POST.get("institution", "").strip()
            user.years_of_experience = request.POST.get("exp") or None
            user.highest_qualification = request.POST.get("qualification")
            user.date_of_birth = request.POST.get("contrib-dob")
            user.bio = request.POST.get("bio", "").strip()

            user.contributor_approval_status = User.ContributorApprovalStatus.PENDING
            user.is_active = False

            user.save()

            raw_expertise = request.POST.get("domain", "")
            save_expertise_and_generate_course_links(user, raw_expertise)

            recommended = recommend_courses_for_contributor(user)
            print("Recommended:", list(recommended.values_list("course_name", flat=True)))

            RegistrationSuccessEmail(user.email, user.first_name).send()
            messages.info(request, "Registration successful. Your contributor account is pending approval.")
            return redirect("pending_approval")

        # Save user
        user.save()
        RegistrationSuccessEmail(user.email, user.first_name).send()
        print(f"SUCCESS: User '{email}' was created and saved.")
        return redirect('register')

    # Default GET render (form page)
    return render(request, 'home/register.html')



def upload_syllabus(request):
    print("Upload view called")
    if request.method == 'POST' and request.FILES.get('pdf_file'):
        pdf_file = request.FILES['pdf_file']

        if not pdf_file.name.lower().endswith('.pdf'):
            messages.error(request, "Only PDF files are allowed.")
            return redirect('upload_syllabus')

        try:
            extract_and_upload(pdf_file)
            messages.success(request, "Syllabus extracted and uploaded successfully!")
        except Exception as e:
            messages.error(request, f"Error: {e}")

    return render(request, 'home/upload_syllabus.html')

def pending_approval_view(request):
    return render(request, "home/pending_approval.html")
