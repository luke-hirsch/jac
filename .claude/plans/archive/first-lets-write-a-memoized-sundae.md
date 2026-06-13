# Portfolio Boilerplate — Step-by-Step Build Guide

Work through these steps in order. Each step is self-contained and verifiable before moving on.

---

## Step 1 — Install dependencies

```bash
# Backend (from backend/)
pip install djangorestframework
pip freeze > ../requirements.txt

# Frontend (from frontend/)
npm install react-router
```

**Verify:** `python -c "import rest_framework"` exits clean.

---

## Step 2 — Create the `portfolio` Django app

```bash
# from backend/
python manage.py startapp portfolio
```

**Then:**

- Add `'rest_framework'` and `'portfolio'` to `INSTALLED_APPS` in `backend/lukehirsch/settings.py`

---

## Step 3 — Write the models (`backend/portfolio/models.py`)

Six models. Replace the file contents entirely:

```python
import uuid
from django.db import models

class ProfileEntry(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    order = models.IntegerField(default=0)
    class Meta: ordering = ['order']
    def __str__(self): return self.key

class Project(models.Model):
    title = models.CharField(max_length=200)
    tagline = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    repo_url = models.URLField(blank=True)
    tags = models.JSONField(default=list)
    featured = models.BooleanField(default=False)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['order', '-created_at']
    def __str__(self): return self.title

class Experience(models.Model):
    role = models.CharField(max_length=200)
    company = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)  # null = current
    description = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    class Meta: ordering = ['order', '-start_date']
    def __str__(self): return f"{self.role} @ {self.company}"

class Skill(models.Model):
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=100, blank=True)
    order = models.IntegerField(default=0)
    class Meta: ordering = ['category', 'order']
    def __str__(self): return self.name

class PortfolioLink(models.Model):
    slug = models.SlugField(max_length=100, unique=True, blank=True, null=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    label = models.CharField(max_length=200)        # internal: "Stripe recruiter Apr 2026"
    created_for = models.CharField(max_length=200)  # name of recipient
    sections = models.JSONField(default=dict)        # {"show": [...], "message": "...", "filter_tags": [...]}
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at']
    def __str__(self): return self.label

class VisitorResponse(models.Model):
    link = models.ForeignKey(PortfolioLink, null=True, blank=True, on_delete=models.SET_NULL)
    path = models.JSONField()   # e.g. ["recruiter", "backend", "contact"]
    ip_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at']
    def __str__(self): return f"{self.path} @ {self.created_at:%Y-%m-%d}"
```

**Then run:**

```bash
python manage.py makemigrations portfolio
python manage.py migrate
```

---

## Step 4 — Register in Admin (`backend/portfolio/admin.py`)

```python
from django.contrib import admin
from .models import ProfileEntry, Project, Experience, Skill, PortfolioLink, VisitorResponse

@admin.register(ProfileEntry)
class ProfileEntryAdmin(admin.ModelAdmin):
    list_display = ('key', 'value', 'order')

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'featured', 'order', 'created_at')
    list_filter = ('featured',)

@admin.register(Experience)
class ExperienceAdmin(admin.ModelAdmin):
    list_display = ('role', 'company', 'start_date', 'end_date')

@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'order')

@admin.register(PortfolioLink)
class PortfolioLinkAdmin(admin.ModelAdmin):
    list_display = ('label', 'created_for', 'slug', 'short_token', 'is_active', 'created_at')
    readonly_fields = ('token', 'created_at')

    @admin.display(description='Token')
    def short_token(self, obj):
        return str(obj.token)[:8] + '…'

@admin.register(VisitorResponse)
class VisitorResponseAdmin(admin.ModelAdmin):
    list_display = ('path', 'link', 'created_at')
    readonly_fields = ('path', 'link', 'ip_hash', 'created_at')
```

**Verify:** `python manage.py runserver` → `/admin/` → all six models visible.

---

## Step 5 — Serializers (`backend/portfolio/serializers.py`)

Create this file:

```python
from rest_framework import serializers
from .models import ProfileEntry, Project, Experience, Skill, PortfolioLink, VisitorResponse

class ProfileEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProfileEntry
        fields = ('key', 'value')

class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ('id', 'title', 'tagline', 'description', 'url', 'repo_url', 'tags', 'featured', 'order')

class ExperienceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Experience
        fields = ('id', 'role', 'company', 'location', 'start_date', 'end_date', 'description')

class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ('id', 'name', 'category')

class PortfolioLinkSerializer(serializers.ModelSerializer):
    projects = serializers.SerializerMethodField()
    experience = serializers.SerializerMethodField()
    skills = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()

    class Meta:
        model = PortfolioLink
        fields = ('slug', 'created_for', 'sections', 'projects', 'experience', 'skills', 'profile')

    def _filter_projects(self, obj):
        qs = Project.objects.all()
        tags = obj.sections.get('filter_tags', [])
        if tags:
            qs = [p for p in qs if any(t in p.tags for t in tags)]
        return ProjectSerializer(qs, many=True).data

    def get_projects(self, obj): return self._filter_projects(obj)
    def get_experience(self, obj): return ExperienceSerializer(Experience.objects.all(), many=True).data
    def get_skills(self, obj): return SkillSerializer(Skill.objects.all(), many=True).data
    def get_profile(self, obj):
        return {e.key: e.value for e in ProfileEntry.objects.all()}

class VisitorResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisitorResponse
        fields = ('link', 'path')
```

---

## Step 6 — Views (`backend/portfolio/views.py`)

```python
import hashlib
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework import status
from .models import ProfileEntry, Project, Experience, Skill, PortfolioLink, VisitorResponse
from .serializers import (
    ProfileEntrySerializer, ProjectSerializer, ExperienceSerializer,
    SkillSerializer, PortfolioLinkSerializer, VisitorResponseSerializer,
)

class ProfileView(APIView):
    def get(self, request):
        entries = ProfileEntry.objects.all()
        return Response({e.key: e.value for e in entries})

class ProjectListView(ListAPIView):
    serializer_class = ProjectSerializer
    def get_queryset(self):
        qs = Project.objects.all()
        if self.request.query_params.get('featured'):
            qs = qs.filter(featured=True)
        return qs

class ExperienceListView(ListAPIView):
    serializer_class = ExperienceSerializer
    queryset = Experience.objects.all()

class SkillListView(APIView):
    def get(self, request):
        skills = Skill.objects.all()
        grouped = {}
        for s in skills:
            grouped.setdefault(s.category or 'Other', []).append(s.name)
        return Response(grouped)

class LinkBySlugView(APIView):
    def get(self, request, slug):
        try:
            link = PortfolioLink.objects.get(slug=slug, is_active=True)
        except PortfolioLink.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(PortfolioLinkSerializer(link).data)

class LinkByTokenView(APIView):
    def get(self, request, token):
        try:
            link = PortfolioLink.objects.get(token=token, is_active=True)
        except PortfolioLink.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(PortfolioLinkSerializer(link).data)

class VisitorResponseView(APIView):
    def post(self, request):
        ip = request.META.get('REMOTE_ADDR', '')
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()
        data = request.data.copy()
        serializer = VisitorResponseSerializer(data=data)
        if serializer.is_valid():
            serializer.save(ip_hash=ip_hash)
            return Response({'status': 'ok'}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
```

---

## Step 7 — URLs (`backend/portfolio/urls.py`)

Create this file:

```python
from django.urls import path
from . import views

urlpatterns = [
    path('profile/', views.ProfileView.as_view()),
    path('projects/', views.ProjectListView.as_view()),
    path('experience/', views.ExperienceListView.as_view()),
    path('skills/', views.SkillListView.as_view()),
    path('links/slug/<slug:slug>/', views.LinkBySlugView.as_view()),
    path('links/token/<uuid:token>/', views.LinkByTokenView.as_view()),
    path('responses/', views.VisitorResponseView.as_view()),
]
```

Wire into the project in `backend/lukehirsch/urls.py`:

```python
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/portfolio/', include('portfolio.urls')),
]
```

**Verify:** `GET http://localhost:8000/api/portfolio/projects/` → `[]`

---

## Step 8 — Vite dev proxy (`frontend/vite.config.ts`)

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
```

---

## Step 9 — React Router setup (`frontend/src/main.tsx`)

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import "./index.css";
import App from "./App.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
```

---

## Step 10 — App routes (`frontend/src/App.tsx`)

```tsx
import { Routes, Route } from "react-router";
import PublicLanding from "./pages/PublicLanding";
import PersonalizedView from "./pages/PersonalizedView";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<PublicLanding />} />
      <Route path="/for/:slug" element={<PersonalizedView mode="slug" />} />
      <Route path="/t/:token" element={<PersonalizedView mode="token" />} />
    </Routes>
  );
}
```

---

## Step 11 — Data-fetching hook (`frontend/src/hooks/usePortfolio.ts`)

```typescript
import { useEffect, useState } from "react";

export function useProfile() {
  const [data, setData] = useState<Record<string, string>>({});
  useEffect(() => {
    fetch("/api/portfolio/profile/")
      .then((r) => r.json())
      .then(setData);
  }, []);
  return data;
}

export function useProjects(featured?: boolean) {
  const [data, setData] = useState<any[]>([]);
  useEffect(() => {
    const url = "/api/portfolio/projects/" + (featured ? "?featured=true" : "");
    fetch(url)
      .then((r) => r.json())
      .then(setData);
  }, [featured]);
  return data;
}

export function usePersonalizedLink(mode: "slug" | "token", id: string) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const endpoint =
      mode === "slug"
        ? `/api/portfolio/links/slug/${id}/`
        : `/api/portfolio/links/token/${id}/`;
    fetch(endpoint)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setData(d);
        setLoading(false);
      });
  }, [mode, id]);
  return { data, loading };
}

export function postResponse(path: string[], linkId?: number) {
  return fetch("/api/portfolio/responses/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, link: linkId ?? null }),
  });
}
```

---

## Step 12 — Public landing page (`frontend/src/pages/PublicLanding.tsx`)

```tsx
import { useState } from "react";
import { postResponse } from "../hooks/usePortfolio";

type Step = { question: string; cards: { label: string; next: string }[] };

const FLOW: Record<string, Step> = {
  start: {
    question: "What brings you here?",
    cards: [
      { label: "Recruiting / hiring", next: "recruiter" },
      { label: "Collaboration", next: "collab" },
      { label: "Just curious", next: "done" },
    ],
  },
  recruiter: {
    question: "What kind of role?",
    cards: [
      { label: "Backend / Python", next: "done" },
      { label: "Full-stack", next: "done" },
      { label: "ML / AI", next: "done" },
    ],
  },
  collab: {
    question: "What kind of project?",
    cards: [
      { label: "Open source", next: "done" },
      { label: "Startup", next: "done" },
      { label: "Freelance", next: "done" },
    ],
  },
};

export default function PublicLanding() {
  const [stepKey, setStepKey] = useState("start");
  const [path, setPath] = useState<string[]>([]);

  function choose(label: string, next: string) {
    const newPath = [...path, label];
    setPath(newPath);
    if (next === "done") {
      postResponse(newPath);
      setStepKey("done");
    } else {
      setStepKey(next);
    }
  }

  if (stepKey === "done") {
    return (
      <section id="center">
        <h1>Let's talk.</h1>
        <p>
          Based on what you're looking for — reach out and I'll send you the
          relevant details.
        </p>
        {/* TODO: show contact / featured projects */}
      </section>
    );
  }

  const step = FLOW[stepKey];
  return (
    <section id="center">
      <h1>{step.question}</h1>
      <div
        style={{
          display: "flex",
          gap: "16px",
          flexWrap: "wrap",
          justifyContent: "center",
        }}
      >
        {step.cards.map((card) => (
          <button
            key={card.label}
            className="counter"
            onClick={() => choose(card.label, card.next)}
          >
            {card.label}
          </button>
        ))}
      </div>
    </section>
  );
}
```

---

## Step 13 — Personalized view (`frontend/src/pages/PersonalizedView.tsx`)

```tsx
import { useParams } from "react-router";
import { usePersonalizedLink } from "../hooks/usePortfolio";

interface Props {
  mode: "slug" | "token";
}

export default function PersonalizedView({ mode }: Props) {
  const params = useParams();
  const id = (mode === "slug" ? params.slug : params.token) ?? "";
  const { data, loading } = usePersonalizedLink(mode, id);

  if (loading)
    return (
      <section id="center">
        <p>Loading…</p>
      </section>
    );
  if (!data)
    return (
      <section id="center">
        <h1>Not found.</h1>
      </section>
    );

  const show: string[] = data.sections?.show ?? [
    "hero",
    "projects",
    "experience",
    "skills",
  ];

  return (
    <main>
      {data.sections?.message && (
        <section id="center">
          <p style={{ fontStyle: "italic" }}>{data.sections.message}</p>
        </section>
      )}

      {show.includes("hero") && (
        <section id="center">
          <h1>{data.profile?.name ?? "Lukas von Hirschhausen"}</h1>
          <p>{data.profile?.tagline ?? ""}</p>
        </section>
      )}

      {show.includes("projects") && data.projects?.length > 0 && (
        <section style={{ padding: "32px" }}>
          <h2>Projects</h2>
          {data.projects.map((p: any) => (
            <div key={p.id} style={{ marginBottom: "24px" }}>
              <h3>{p.title}</h3>
              <p>{p.tagline}</p>
              {p.url && <a href={p.url}>View project →</a>}
            </div>
          ))}
        </section>
      )}

      {show.includes("experience") && data.experience?.length > 0 && (
        <section style={{ padding: "32px" }}>
          <h2>Experience</h2>
          {data.experience.map((e: any) => (
            <div key={e.id} style={{ marginBottom: "16px" }}>
              <strong>{e.role}</strong> — {e.company}
              <p>{e.description}</p>
            </div>
          ))}
        </section>
      )}

      {show.includes("skills") && data.skills && (
        <section style={{ padding: "32px" }}>
          <h2>Skills</h2>
          {Object.entries(data.skills).map(([cat, items]: [string, any]) => (
            <div key={cat}>
              <strong>{cat}:</strong> {(items as string[]).join(", ")}
            </div>
          ))}
        </section>
      )}
    </main>
  );
}
```

---

## Step 14 — Smoke test everything

```bash
# Terminal 1
cd backend && python manage.py runserver

# Terminal 2
cd frontend && npm run dev
```

Checklist:

- [ ] `http://localhost:5173/` — choose-your-path cards render, clicking advances steps
- [ ] Choosing a final card → POST hits `/api/portfolio/responses/` (check Network tab) → row appears in `/admin/portfolio/visitorresponse/`
- [ ] Create a `PortfolioLink` in Admin with slug `test` and `sections = {"show": ["hero","projects"]}`
- [ ] Visit `http://localhost:5173/for/test` → PersonalizedView renders with the message
- [ ] `npm run build` → no TypeScript errors

---

## What's not done yet (next steps)

- Styling the components beyond the existing CSS vars
- `ProjectCard`, `ExperienceItem` extracted as proper components
- Populating Admin with real content
- JAC → `PortfolioLink` auto-creation (after JAC app is wired)
- `llm_connector` wired to JAC prompt logic
