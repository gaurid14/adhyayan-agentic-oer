# working but giving  → Created Expertise: Programming (3 courses)
#  → Created Expertise: Data (3 courses)
#  → Created Expertise: Systems (3 courses)
#  → Created Expertise: Technologies (3 courses)
#  → Created Expertise: Security (2 courses)
#  → Created Expertise: Machine (2 courses)
#  → Created Expertise: Digital (1 courses)
#  → Created Expertise: Embedded (1 courses)

# from ...models import Program, Course, Expertise
# from sentence_transformers import SentenceTransformer
# from sklearn.cluster import KMeans
# from sklearn.metrics import silhouette_score
# import numpy as np
#
# def generate_expertise(min_courses=3, max_clusters=10):
#     """
#     Generate program-wise expertise clusters using semantic similarity of course names.
#     Clears old expertises for the program and creates new ones.
#     """
#     model = SentenceTransformer('all-MiniLM-L6-v2')  # embeddings model
#
#     for program in Program.objects.all():
#         print(f"\nProcessing Program: {program.program_name}")
#
#         courses = list(Course.objects.filter(department__program=program))
#         if len(courses) < min_courses:
#             print(f"Skipping {program.program_name}: Not enough courses")
#             continue
#
#         # Get course names
#         course_names = [c.course_name for c in courses]
#         embeddings = model.encode(course_names)
#
#         # Determine optimal number of clusters
#         best_k = min(len(courses), max_clusters)
#         best_score = -1
#         best_labels = None
#
#         for k in range(2, best_k + 1):
#             kmeans = KMeans(n_clusters=k, random_state=42, n_init='auto')
#             labels = kmeans.fit_predict(embeddings)
#             if len(set(labels)) > 1:  # avoid single cluster errors
#                 score = silhouette_score(embeddings, labels)
#                 if score > best_score:
#                     best_score = score
#                     best_labels = labels
#
#         if best_labels is None:
#             print(f"Could not cluster {program.program_name}")
#             continue
#
#         # Clear old expertises for this program
#         Expertise.objects.filter(program=program).delete()
#
#         clusters = {}
#         for label, course in zip(best_labels, courses):
#             clusters.setdefault(label, []).append(course)
#
#         for i, (label, grouped_courses) in enumerate(clusters.items(), start=1):
#             # Generate generic expertise name (most common word)
#             names = [c.course_name for c in grouped_courses]
#             keywords = [word for name in names for word in name.split() if len(word) > 3]
#             if keywords:
#                 generic_name = max(set(keywords), key=keywords.count).capitalize()
#             else:
#                 generic_name = f"Expertise {i}"
#
#             expertise = Expertise.objects.create(
#                 program=program,
#                 name=generic_name
#             )
#             expertise.courses.add(*grouped_courses)
#
#             print(f" → Created Expertise: {generic_name} ({len(grouped_courses)} courses)")
#
#     print("\n✅ Expertise generation complete!")
#



from ...models import Program, Course, Expertise
from sentence_transformers import SentenceTransformer, util
from sklearn.cluster import AgglomerativeClustering
import numpy as np
import re

def clean_title(title: str):
    """Clean course title for expertise naming."""
    title = re.sub(r"(?i)\b(introduction|fundamentals|basics|principles|overview|concepts|advanced)\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title.title()

def generate_expertise(min_courses=3, similarity_threshold=0.45):
    """
    Generate program-wise expertise clusters using semantic similarity of
    course names + objectives + outcomes.
    """
    print("Starting smart expertise generation...")
    model = SentenceTransformer('all-MiniLM-L6-v2')

    for program in Program.objects.all():
        print(f"\n🔹 Processing Program: {program.program_name}")

        courses = list(Course.objects.filter(department__program=program))
        if len(courses) < min_courses:
            print(f"Skipping {program.program_name}: Not enough courses")
            continue

        # Combine course title + objectives + outcomes for better semantic context
        course_texts = []
        for c in courses:
            objectives_text = " ".join([o.description for o in c.objectives.all()])
            outcomes_text = " ".join([co.description for co in c.outcomes.all()])
            full_text = f"{c.course_name}. {objectives_text}. {outcomes_text}"
            course_texts.append(full_text.strip())

        embeddings = model.encode(course_texts, normalize_embeddings=True)

        # Agglomerative clustering
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=1 - similarity_threshold,
            affinity='cosine',
            linkage='average'
        )
        labels = clustering.fit_predict(embeddings)

        # Clear old expertises
        Expertise.objects.filter(program=program).delete()

        clusters = {}
        for label, course in zip(labels, courses):
            clusters.setdefault(label, []).append(course)

        # Create Expertise objects for each cluster
        for i, (label, grouped_courses) in enumerate(clusters.items(), start=1):
            if not grouped_courses:
                continue

            # Representative course for naming
            idxs = [courses.index(c) for c in grouped_courses]
            cluster_embs = embeddings[idxs]
            centroid = np.mean(cluster_embs, axis=0)
            sims = util.cos_sim(centroid, cluster_embs)[0]
            rep_idx = int(np.argmax(sims))
            rep_course = grouped_courses[rep_idx]

            rep_name = clean_title(rep_course.course_name)
            expertise = Expertise.objects.create(program=program, name=rep_name)
            expertise.courses.add(*grouped_courses)
            print(f"Expertise: {rep_name} ({len(grouped_courses)} courses)")

        # Add miscellaneous expertise for unclustered courses
        all_clustered = {c.id for group in clusters.values() for c in group}
        unclustered = [c for c in courses if c.id not in all_clustered]
        if unclustered:
            misc = Expertise.objects.create(program=program, name="Miscellaneous")
            misc.courses.add(*unclustered)
            print(f"Added {len(unclustered)} unclustered courses to Miscellaneous.")

    print("\nExpertise generation complete!")


