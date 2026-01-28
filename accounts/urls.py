from django.urls import path
from .views import views
from .views.contributor import generate_expertise
from .views.contributor.contributor_dashboard import contributor_dashboard_view, contributor_submit_content_view, \
    contributor_profile, contributor_submissions
from .views.contributor.submit_content import upload_files, load_file, contributor_editor, delete_drive_file, \
    confirm_submission, gemini_chat, contributor_upload_file, \
    generate_assessment, after_submission, generated_assessment_form
from .views.home.home import about, contact
from .views.home.subjects import subject_view, chapter_view
from .views.forum import (
    forum_home, forum_detail, post_question, post_answer, post_reply,
    toggle_question_upvote, toggle_answer_upvote,
    dm_inbox, dm_thread,     # <-- ADD THIS IMPORT
)

urlpatterns = [
    path('', views.home_view, name='home'),  # Home page at "/"
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    # path('dashboard/', views.dashboard_view, name='dashboard'),
    path('upload/', views.upload_syllabus, name='upload_syllabus'),
    path('about/', about, name='about'),
    path('contact/', contact, name='contact'),
    path('course/<int:course_id>/chapters/', chapter_view, name='chapter_view'),
    path('subject/<str:stream>/<int:year>/<str:semester>/', subject_view, name='subject_view'),
    path('dashboard/contributor/', contributor_dashboard_view, name='contributor_dashboard'),
    path('dashboard/contributor/profile/', contributor_profile, name='contributor_profile'),
    path('dashboard/contributor/submissions/', contributor_submissions, name='contributor_submissions'),
    path('dashboard/contributor/submit_content/', contributor_submit_content_view, name='contributor_submit_content_view'),
    path('dashboard/contributor/submit_content/upload/submission', contributor_upload_file, name='contributor_upload_file'),
    path('dashboard/contributor/submit_content/upload', upload_files, name='upload_files'),
    path('dashboard/contributor/submit_content/uploadDraft', contributor_editor, name='contributor_editor'),
    path('dashboard/contributor/submit_content/load_file', load_file, name='load_file'),  # needed for JS
    path('dashboard/contributor/submit_content/delete_file', delete_drive_file, name='delete_drive_file'),  # needed for JS
    # path('dashboard/contributor/submit_content/submit_assessment', submit_assessment, name='submit_assessment'),
    path('dashboard/contributor/submit_content/gemini_chat', gemini_chat, name='gemini_chat'),
    path('dashboard/contributor/submit_content/after_submission/', after_submission, name='after_submission'),
    path('dashboard/student/', views.dashboard_view, name='student_dashboard'),

    # Forum
    path("forum/", forum_home, name="forum_home"),
    path("forum/<int:pk>/", forum_detail, name="forum_detail"),
    path("forum/ask/", post_question, name="forum_ask"),
    path("forum/<int:question_id>/answer/", post_answer, name="forum_answer"),
    path("forum/<int:question_id>/reply/<int:parent_id>/", post_reply, name="forum_reply"),
    path("forum/<int:pk>/upvote/", toggle_question_upvote, name="forum_question_upvote"),
    path("forum/answer/<int:pk>/upvote/", toggle_answer_upvote, name="forum_answer_upvote"),
    # path('generate-assessment/<int:chapter_id>/', generate_assessment_view, name='generate_assessment'), # <-- ADD THIS LINE

    # --- Redirect/Display Pages ---
    # path('contributor/submission-complete/', after_submission_view, name='after_submission'), # Thank you page
    # Assessment page (might need chapter_id or upload_id)
    path('contributor/generate_assessment', generate_assessment, name='generate_assessment'),
    path('contributor/generated_assessment/<int:assessment_id>/', generated_assessment_form, name='generated_assessment_form'),
    path("contributor/confirm_submission", confirm_submission, name="confirm_submission"),

    # DMs
    path("messages/", dm_inbox, name="dm_inbox"),
    path("messages/<int:user_id>/", dm_thread, name="dm_thread"),

    path('pending-approval/', views.pending_approval_view, name='pending_approval'),

]
