# OER/accounts/models.py

from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from django.db.models import UniqueConstraint
from django.utils import timezone

# Syllabus
class Program(models.Model):
    program_name = models.CharField(max_length=200, unique=True)


    # --- Forum moderation / suspension ---
    forum_is_suspended = models.BooleanField(default=False)
    forum_suspended_at = models.DateTimeField(null=True, blank=True)
    forum_suspension_reason = models.TextField(blank=True)

    def __str__(self):
        return self.program_name


class Department(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="departments")
    dept_name = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.dept_name} ({self.program.program_name})"

class Scheme(models.Model):
    name = models.CharField(max_length=200, unique=True)  # e.g., "Revised C19"
    start_year = models.IntegerField()  # year when this scheme started
    end_year = models.IntegerField(blank=True, null=True)  # optional if ongoing

    def __str__(self):
        return self.name


class Course(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="courses")
    scheme = models.ForeignKey(Scheme, on_delete=models.CASCADE, related_name="courses")
    course_code = models.CharField(max_length=20)
    course_name = models.CharField(max_length=200)
    year_of_study = models.CharField(null=True, blank=True)
    semester = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('scheme', 'course_code')  # same code can exist in different schemes

    def __str__(self):
        return f"{self.course_code} - {self.course_name} ({self.scheme.name}, Year {self.year_of_study}, Sem {self.semester})"



class Chapter(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="chapters")
    chapter_number = models.IntegerField()
    chapter_name = models.CharField(max_length=200)
    description = models.CharField(max_length=10000, default="No description available")

    class Meta:
        unique_together = ('course', 'chapter_number')

    def __str__(self):
        return f"{self.course.course_name} | Ch {self.chapter_number}: {self.chapter_name}"

# ---------- Chapter timeline / policy -------------------------------------------------

class ChapterPolicy(models.Model):
    """Admin-controlled timeline & contribution targets for a chapter."""

    chapter = models.OneToOneField(
        Chapter,
        on_delete=models.CASCADE,
        related_name="policy",
    )

    # timeline
    deadline = models.DateTimeField(null=True, blank=True)
    current_deadline = models.DateTimeField(null=True, blank=True)

    # contribution targets
    min_contributions = models.PositiveIntegerField(default=1)

    # extension policy
    max_extensions = models.PositiveIntegerField(default=0)
    max_days_per_extension = models.PositiveIntegerField(default=0)
    extensions_used = models.PositiveIntegerField(default=0)

    def save(self, *args, **kwargs):
        # initialize current_deadline the first time
        if self.deadline and not self.current_deadline:
            self.current_deadline = self.deadline
        super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        if not self.current_deadline:
            return True
        return timezone.now() <= self.current_deadline

    def __str__(self):
        return f"Policy: {self.chapter}"


class ChapterDeadlineExtension(models.Model):
    policy = models.ForeignKey(
        ChapterPolicy,
        on_delete=models.CASCADE,
        related_name="extensions",
    )
    extended_by = models.ForeignKey(
        "User",  # ✅ string ref so it works even if User is declared later
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chapter_deadline_extensions",
    )
    extended_at = models.DateTimeField(auto_now_add=True)

    days_extended = models.PositiveIntegerField()
    old_deadline = models.DateTimeField()
    new_deadline = models.DateTimeField()
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-extended_at"]

    def __str__(self):
        return f"{self.policy.chapter} +{self.days_extended}d"

class CourseObjective(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="objectives")
    objective_code = models.CharField(max_length=20, blank=True, null=True)  # e.g., O1, O2 (optional)
    description = models.TextField()

    def __str__(self):
        if self.objective_code:
            return f"{self.course.course_name} - {self.objective_code}"
        return f"{self.course.course_name} - Objective"


class CourseOutcome(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="outcomes")
    outcome_code = models.CharField(max_length=20)   # e.g., CO1, CO2
    description = models.TextField()

    def __str__(self):
        return f"{self.course.course_name} - {self.outcome_code}"


class OutcomeChapterMapping(models.Model):
    outcome = models.ForeignKey(CourseOutcome, on_delete=models.CASCADE, related_name="chapter_mappings")
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="outcome_mappings")

    def __str__(self):
        return f"{self.outcome.outcome_code} ↔ {self.chapter.chapter_name}"

# Contributor's expertise
class Expertise(models.Model):
    name = models.CharField(max_length=150, unique=True)  # generic expertise
    courses = models.ManyToManyField('Course', related_name='expertises', blank=True)  # automatically linked

    def __str__(self):
        return f"{self.name}"


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "STUDENT", "Student"
        CONTRIBUTOR = "CONTRIBUTOR", "Contributor"

    # COURSE_CHOICES = [
    #     ('IT', 'IT'),
    #     ('CS', 'CS'),
    #     ('MECH', 'MECH'),
    #     ('EXTC', 'EXTC'),
    # ]

    # --- FIX: Updated YEAR_CHOICES to match your new dropdown ---
    YEAR_CHOICES = [
        ('1', 'FE'),
        ('2', 'SE'),
        ('3', 'TE'),
        ('4', 'BE'),
    ]

    HIGHEST_QUALIFICATION_CHOICES = [
        ('BACHELORS', 'Bachelor’s Degree'),
        ('MASTERS', 'Master’s Degree'),
        ('PHD', 'PhD'),
        ('OTHER', 'Other'),
    ]
    class ContributorApprovalStatus(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        PENDING = "PENDING", "Pending"
        REJECTED = "REJECTED", "Rejected"

    role = models.CharField(max_length=50, choices=Role.choices, default=Role.STUDENT)

    # Student Fields
    college_name = models.CharField(max_length=200, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=10, blank=True, null=True)
    program = models.ForeignKey(
        Program, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='students')
    year = models.CharField(max_length=50, choices=YEAR_CHOICES, blank=True, null=True)

    # Contributor Fields
    profile_picture = models.ImageField(upload_to='profile_pics/', blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True, null=True)
    years_of_experience = models.IntegerField(blank=True, null=True)
    domain_of_expertise = models.ManyToManyField('Expertise', blank=True, related_name="experts")
    highest_qualification = models.CharField(max_length=50, choices=HIGHEST_QUALIFICATION_CHOICES, blank=True, null=True)
    current_institution = models.CharField(max_length=200, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)

    groups = models.ManyToManyField('auth.Group', related_name='custom_user_set', blank=True)
    user_permissions = models.ManyToManyField('auth.Permission', related_name='custom_user_permission_set', blank=True)

    contributor_approval_status = models.CharField(
        max_length=20,
        choices=ContributorApprovalStatus.choices,
        default=ContributorApprovalStatus.APPROVED,   # students & existing users unaffected
    )
    contributor_approved_at = models.DateTimeField(null=True, blank=True)
    contributor_rejected_at = models.DateTimeField(null=True, blank=True)
    contributor_rejection_reason = models.TextField(blank=True)

    def __str__(self):
        return self.username



# Content checks

class UploadCheck(models.Model):
    contributor = models.ForeignKey(
        User, on_delete=models.CASCADE,
        limit_choices_to={'role': 'CONTRIBUTOR'},
        related_name="uploads"
    )
    chapter = models.ForeignKey(
        Chapter, on_delete=models.CASCADE,
        related_name="uploads"
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    evaluation_status = models.BooleanField(default=False)

    def __str__(self):
        return f"Upload by {self.contributor.username} for {self.chapter.chapter_name} at {self.timestamp}"


class ContentCheck(models.Model):
    upload = models.OneToOneField(  # One upload → One content check row
        UploadCheck, on_delete=models.CASCADE,
        related_name="content_check"
    )
    pdf = models.BooleanField(default=False)
    video = models.BooleanField(default=False)
    assessment = models.BooleanField(default=False)

    extraction_status = models.BooleanField(default=False)
    extraction_updated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Content Check for Upload {self.upload.id} | PDF:{self.pdf} Video:{self.video} Assessment:{self.assessment}"


# Evaluation and release

class ContentScore(models.Model):
    upload = models.OneToOneField(
        UploadCheck, on_delete=models.CASCADE, related_name="content_score"
    )

    engagement = models.FloatField(blank=True, null=True)
    clarity = models.FloatField(blank=True, null=True)
    coherence = models.FloatField(blank=True, null=True)
    completeness = models.FloatField(blank=True, null=True)
    accuracy = models.FloatField(blank=True, null=True)

    # ✅ add back (needed by decision maker mark-best feature)
    is_best = models.BooleanField(default=False)

class ReleasedContent(models.Model):
    upload = models.OneToOneField(
        UploadCheck, on_delete=models.CASCADE,
        related_name="released_content"
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    drive_folder_id = models.CharField(max_length=255, null=True, blank=True)
    release_status = models.BooleanField(default=False)

    def __str__(self):
        return f"Released? {self.release_status} for Upload {self.upload.id}"

class DecisionRun(models.Model):
    """Audit record for a decision-maker run selecting the best upload for a chapter."""

    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.CASCADE,
        related_name="decision_runs",
    )
    selected_upload = models.ForeignKey(
        "UploadCheck",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decision_runs_selected",
    )
    decided_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decision_runs_decided",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=32, default="ok")
    strategy = models.CharField(max_length=64, default="weighted_average")
    weights = models.JSONField(default=dict, blank=True)
    thresholds = models.JSONField(default=dict, blank=True)

    composite_score = models.FloatField(null=True, blank=True)
    ranking = models.JSONField(default=list, blank=True)
    explanation = models.TextField(blank=True, default="")

    is_latest = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"DecisionRun(chapter_id={self.chapter_id}, selected_upload_id={self.selected_upload_id}, score={self.composite_score})"



# Student Enrollment into Course

class EnrolledCourse(models.Model):
    student = models.ForeignKey(
        User, on_delete=models.CASCADE,
        limit_choices_to={'role': 'STUDENT'},
        related_name="enrollments"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE,
        related_name="enrolled_students"
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('student', 'course')  # prevent duplicate enrollments

    def __str__(self):
        return f"{self.student.username} enrolled in {self.course.course_name}"



class Assessment(models.Model):
    course = models.ForeignKey('Course', on_delete=models.CASCADE)
    chapter = models.ForeignKey('Chapter', on_delete=models.CASCADE)
    topic = models.CharField(max_length=100, blank=True, null=True)
    contributor_id = models.ForeignKey('User', on_delete=models.CASCADE)


class Question(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    correct_option = models.IntegerField()  # stores index of correct option

class Option(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=255)

class AssessmentSource(models.Model):
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE
    )
    drive_file_id = models.CharField(max_length=255)
    file_name = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)

# ---------- Forum Models --------------------------------------------------------------------------------------

class ForumTopic(models.Model):
    """Represents syllabus topics or chapters used for tagging questions."""
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

class ModerationStatus(models.TextChoices):
    APPROVED = "approved", "Approved"
    PENDING = "pending_review", "Pending review"
    REJECTED = "rejected", "Rejected"

class ForumQuestion(models.Model):
    """Main question/discussion post."""
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="forum_questions")
    title = models.CharField(max_length=255)
    content = models.TextField()
    topics = models.ManyToManyField(ForumTopic, blank=True, related_name="questions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    upvotes = models.ManyToManyField(User, related_name="question_upvotes", blank=True)
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        null=True,          # keep null=True so old questions don't break migrations
        blank=False,
        related_name="forum_questions",
    )
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="forum_questions",
    )

    # ---- Moderation (auto + manual review) ----
    is_hidden = models.BooleanField(default=False)

    moderation_status = models.CharField(
        max_length=32,
        choices=ModerationStatus.choices,
        default=ModerationStatus.APPROVED,
    )

    moderation_model = models.CharField(max_length=64, null=True, blank=True)
    moderation_details = models.JSONField(default=dict, blank=True)
    moderated_at = models.DateTimeField(null=True, blank=True)


    def __str__(self):
        return self.title

    @property
    def total_upvotes(self):
        return self.upvotes.count()


class ForumAnswer(models.Model):
    """Answers or replies to a question."""
    question = models.ForeignKey(ForumQuestion, on_delete=models.CASCADE, related_name="answers")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="forum_answers")
    content = models.TextField()
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='child_comments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    upvotes = models.ManyToManyField(User, related_name="answer_upvotes", blank=True)

    # ---- Moderation (auto + manual review) ----
    is_hidden = models.BooleanField(default=False)

    moderation_status = models.CharField(
        max_length=32,
        choices=ModerationStatus.choices,
        default=ModerationStatus.APPROVED,
    )

    moderation_model = models.CharField(max_length=64, null=True, blank=True)
    moderation_details = models.JSONField(default=dict, blank=True)
    moderated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Answer by {self.author.username} on {self.question.title}"

    @property
    def total_upvotes(self):
        return self.upvotes.count()

    class Meta:
        ordering = ["created_at"]  # oldest first; flip to ["-created_at"] if you prefer

    @property
    def children(self):
        # Use prefetched data if available (avoids N+1)
        return self.child_comments.all()

class DmThread(models.Model):
    """
    A canonical thread between two users.
    Enforced uniqueness regardless of order (user_a, user_b).
    """
    user_a = models.ForeignKey(User, on_delete=models.CASCADE, related_name="dm_threads_as_a")
    user_b = models.ForeignKey(User, on_delete=models.CASCADE, related_name="dm_threads_as_b")
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["user_a", "user_b"], name="uniq_dm_pair")
        ]

    def save(self, *args, **kwargs):
        # always store with smaller id in user_a
        if self.user_b_id and self.user_a_id and self.user_b_id < self.user_a_id:
            self.user_a_id, self.user_b_id = self.user_b_id, self.user_a_id
        super().save(*args, **kwargs)

    def other_of(self, user):
        return self.user_b if user == self.user_a else self.user_a

    def __str__(self):
        return f"DM: {self.user_a.username} ↔ {self.user_b.username}"


class DmMessage(models.Model):
    thread = models.ForeignKey("DmThread", related_name="messages", on_delete=models.CASCADE)
    sender = models.ForeignKey("User", on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])

# External Resources


# ---------- Reporting / Blocking --------------------------------------------------------------------------------------

class ReportCaseKind(models.TextChoices):
    QUESTION = "question", "Question"
    ANSWER = "answer", "Answer / Reply"
    USER = "user", "User"
    DM_MESSAGE = "dm_message", "Direct message"


class ReportCaseStatus(models.TextChoices):
    OPEN = "open", "Open"
    ACTIONED = "actioned", "Action taken"
    DISMISSED = "dismissed", "Dismissed"


class ReportReason(models.TextChoices):
    ABUSE_HARASSMENT = "abuse_harassment", "Abusive / Harassing"
    HATE_DISCRIMINATION = "hate_discrimination", "Hate / Discrimination"
    VIOLENCE_THREAT = "violence_threat", "Violence / Threat"
    SEXUAL_OBSCENE = "sexual_obscene", "Sexual / Obscene"
    SPAM = "spam", "Spam"
    PRIVACY = "privacy", "Privacy / Personal info"
    CHEATING = "cheating", "Cheating / Academic dishonesty"
    OTHER = "other", "Other"


class ReportCase(models.Model):
    """
    A rollup case for a single target (question / answer / user / DM message),
    containing aggregated counts and (for DM) a snapshot of the last 5 messages.
    """
    kind = models.CharField(max_length=20, choices=ReportCaseKind.choices)
    target_key = models.CharField(max_length=40, unique=True)

    status = models.CharField(
        max_length=20,
        choices=ReportCaseStatus.choices,
        default=ReportCaseStatus.OPEN,
    )
    needs_review = models.BooleanField(default=False)

    total_reports = models.PositiveIntegerField(default=0)
    distinct_reporters = models.PositiveIntegerField(default=0)

    last_5_messages = models.JSONField(null=True, blank=True)

    # Targets (only one should be set, based on kind)
    question = models.ForeignKey(
        "ForumQuestion",
        on_delete=models.CASCADE,
        related_name="report_cases",
        null=True,
        blank=True,
    )
    answer = models.ForeignKey(
        "ForumAnswer",
        on_delete=models.CASCADE,
        related_name="report_cases",
        null=True,
        blank=True,
    )
    dm_message = models.ForeignKey(
        "DmMessage",
        on_delete=models.CASCADE,
        related_name="report_cases",
        null=True,
        blank=True,
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reported_cases",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def recompute_counts(self):
        qs = self.reports.all()
        self.total_reports = qs.count()
        self.distinct_reporters = qs.values("reporter_id").distinct().count()

    def __str__(self):
        return f"{self.kind}:{self.target_key} ({self.status})"


class Report(models.Model):
    case = models.ForeignKey("ReportCase", on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_made")

    reason = models.CharField(max_length=30, choices=ReportReason.choices)
    note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("case", "reporter"),)

    def __str__(self):
        return f"Report by {self.reporter_id} on {self.case_id}"


class UserBlock(models.Model):
    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_made")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocked_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("blocker", "blocked"),)

    def __str__(self):
        return f"{self.blocker_id} blocked {self.blocked_id}"


class ExternalResource(models.Model):
    course = models.ForeignKey("Course", on_delete=models.CASCADE)
    chapter = models.ForeignKey("Chapter", on_delete=models.CASCADE)
    topic = models.CharField(max_length=255)

    title = models.CharField(max_length=255)
    url = models.URLField()

    resource_type = models.CharField(
        max_length=50,
        choices=[
            ("youtube", "YouTube"),
            ("article", "Article"),
            ("docs", "Docs/Notes"),
            ("other", "Other"),
        ],
        default="youtube"
    )

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        limit_choices_to={"role": "CONTRIBUTOR"},
        related_name="external_resources"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.resource_type})"


# For auto-submission, track contributor's progress
class ChapterContributionProgress(models.Model):
    """
    Tracks upload progress of a contributor for a chapter.
    Created on first file upload.
    Used for auto-submit & deadline handling.
    """

    contributor = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "CONTRIBUTOR"},
        related_name="chapter_progress"
    )

    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.CASCADE,
        related_name="contributor_progress"
    )

    # ---- upload tracking ----
    pdf_count = models.PositiveIntegerField(default=0)
    video_count = models.PositiveIntegerField(default=0)
    draft_count = models.PositiveIntegerField(default=0)

    # ---- lifecycle flags ----
    has_any_upload = models.BooleanField(default=False)
    auto_submitted = models.BooleanField(default=False)

    first_upload_at = models.DateTimeField(auto_now_add=True)
    last_upload_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("contributor", "chapter")

    def __str__(self):
        return f"{self.contributor.username} → {self.chapter}"

    @property
    def total_uploads(self):
        return self.pdf_count + self.video_count

# Quick notes for contributor
class ContributorNote(models.Model):
    contributor = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notes",
        limit_choices_to={"role": "CONTRIBUTOR"}
    )

    title = models.CharField(max_length=200, blank=True)
    content = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Note by {self.contributor.username}: {self.title or 'Untitled'}"


# python manage.py makemigrations
# python manage.py migrate



class ReleasePolicy(models.Model):
    """
    Controls course-level release threshold.
    """

    course = models.OneToOneField(
        Course,
        on_delete=models.CASCADE,
        related_name="release_policy"
    )

    threshold_percentage = models.IntegerField(default=80)

    auto_release_enabled = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.course.course_name} - {self.threshold_percentage}%"

from django.db.models.signals import post_save
from django.dispatch import receiver
from accounts.services.decision_maker import DecisionMakerService
# from accounts.models import DecisionRun

@receiver(post_save, sender=ContentScore)
def auto_run_decision_maker(sender, instance, created, **kwargs):
    if created:
        DecisionMakerService().decide_for_chapter(
            chapter_id=instance.upload.chapter.id,
            force=True
        )

# Remove the import from the top of the file! 
# Delete: from accounts.services.admin_agent import run_admin_release_for_course

@receiver(post_save, sender=DecisionRun)
def auto_run_admin_agent(sender, instance, created, **kwargs):
    if created:
        # Move import here to prevent Circular Import error
        from accounts.services.admin_agent import AdminAgentService
        
        course = instance.chapter.course
        # Instantiate the service and call the new method
        service = AdminAgentService()
        service.run_for_course(course)





# python manage.py makemigrations
# python manage.py migrate