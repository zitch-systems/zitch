"""Turn the AI on for customers who linked before it defaulted on.

The flag started as opt-in consent granted by replying "ai on". In practice
nobody discovered the phrase, so every existing customer sits at off — meaning
natural language answers "Sorry, I didn't get that" for all of them while the
console reports the AI as live. A consent nobody can find is not a choice.

They can still turn it off, and the global kill switch still governs everyone.
"""
from django.db import migrations


def enable_for_existing(apps, schema_editor):
    apps.get_model("whatsapp", "WhatsAppLink").objects.filter(ai_enabled=False).update(ai_enabled=True)


class Migration(migrations.Migration):
    dependencies = [("whatsapp", "0011_alter_whatsapplink_ai_enabled")]
    operations = [migrations.RunPython(enable_for_existing, migrations.RunPython.noop)]
