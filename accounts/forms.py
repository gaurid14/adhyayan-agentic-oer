from django import forms

from .models import (
    Course,
    Chapter,
    ForumQuestion,
    ForumAnswer,
    ForumTopic,
    Program,
    Department,
    User,
)


class ForumQuestionForm(forms.ModelForm):
    """
    Ask question form with Course + dependent Chapter dropdown.

    Important: Chapter queryset must be filtered based on the selected course,
    otherwise users can choose any chapter from any course.
    """

    class Meta:
        model = ForumQuestion
        fields = ["title", "content", "course", "chapter", "topics"]

        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Write a clear title",
                "maxlength": "255",
                "id": "id_title",
            }),
            "content": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Explain your question in detail (what you tried, error, etc.)",
                "rows": 6,
                "id": "id_content",
            }),
            "course": forms.Select(attrs={
                "class": "form-select",
                "id": "id_course",
            }),
            "chapter": forms.Select(attrs={
                "class": "form-select",
                "id": "id_chapter",
            }),
            "topics": forms.SelectMultiple(attrs={
                "class": "form-select",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Default: no chapters until a course is chosen
        self.fields["chapter"].queryset = Chapter.objects.none()
        self.fields["chapter"].required = False  # chapter optional

        # If course is selected in GET/POST data (user changed dropdown)
        course_id = None
        if "course" in self.data:
            try:
                course_id = int(self.data.get("course"))
            except (TypeError, ValueError):
                course_id = None

        # Or when editing an existing object (instance already has course)
        if course_id is None and getattr(self.instance, "pk", None):
            if self.instance.course_id:
                course_id = self.instance.course_id

        # Apply chapter filtering if we have course_id
        if course_id:
            self.fields["chapter"].queryset = (
                Chapter.objects
                .filter(course_id=course_id)
                .order_by("chapter_number", "chapter_name")
            )

class ForumAnswerForm(forms.ModelForm):
    class Meta:
        model = ForumAnswer
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Write an answer or reply..."}),
        }


class ForumTopicForm(forms.ModelForm):
    class Meta:
        model = ForumTopic
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., CO1, Bloom’s, Ch-3"}),
        }

class ProfilePictureForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['profile_picture']
        labels = {'profile_picture': 'Upload a new profile picture'}

class StudentProfileForm(forms.ModelForm):
    GENDER_CHOICES = [
        ("", "Select gender"),
        ("Male", "Male"),
        ("Female", "Female"),
        ("Other", "Other"),
        ("Prefer not to say", "Prefer not to say"),
    ]

    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False)

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "phone_number",
            "college_name",
            "date_of_birth",
            "gender",
            "program",
            "department",
            "year",
            "bio",
            "profile_picture",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-input", "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Last name"}),
            "phone_number": forms.TextInput(attrs={"class": "form-input", "placeholder": "Phone number"}),
            "college_name": forms.TextInput(attrs={"class": "form-input", "placeholder": "College name"}),
            "date_of_birth": forms.DateInput(attrs={"class": "form-input", "type": "date"}),
            "program": forms.Select(attrs={"class": "form-input"}),
            "department": forms.Select(attrs={"class": "form-input"}),
            "year": forms.Select(attrs={"class": "form-input"}),
            "bio": forms.Textarea(attrs={"class": "form-input", "rows": 4, "placeholder": "Write a short bio"}),
            "profile_picture": forms.ClearableFileInput(attrs={"class": "form-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["program"].queryset = Program.objects.all().order_by("program_name")
        self.fields["department"].queryset = Department.objects.select_related("program").order_by("dept_name")

    def clean(self):
        cleaned_data = super().clean()
        program = cleaned_data.get("program")
        department = cleaned_data.get("department")

        if department and not program:
            cleaned_data["program"] = department.program
            program = cleaned_data["program"]

        if department and program and department.program_id != program.id:
            self.add_error("department", "Please choose a department that belongs to the selected program.")
        return cleaned_data
