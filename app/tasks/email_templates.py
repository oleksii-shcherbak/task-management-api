def verification_email(name: str, verify_url: str) -> str:
    return f"""\
<html>
  <body style="font-family: sans-serif; color: #222;">
    <p>Hi {name},</p>
    <p>Click the link below to verify your email address. The link expires in 24 hours.</p>
    <p><a href="{verify_url}">{verify_url}</a></p>
    <p>If you did not create an account, you can ignore this email.</p>
  </body>
</html>"""


def password_reset_email(name: str, token: str) -> str:
    return f"""\
<html>
  <body style="font-family: sans-serif; color: #222;">
    <p>Hi {name},</p>
    <p>Use the token below to reset your password. It expires in 1 hour.</p>
    <p style="font-size: 1.2em; font-family: monospace; background: #f4f4f4; padding: 8px;">
      {token}
    </p>
    <p>Send a POST request to <code>/api/v1/auth/reset-password</code> with this token and your new password.</p>
    <p>If you did not request a password reset, you can ignore this email.</p>
  </body>
</html>"""


def due_date_reminder_email(
    name: str, task_title: str, project_name: str, due_date: str
) -> str:
    return f"""\
<html>
  <body style="font-family: sans-serif; color: #222;">
    <p>Hi {name},</p>
    <p>
      This is a reminder that the task <strong>{task_title}</strong>
      in project <strong>{project_name}</strong> is due on <strong>{due_date}</strong>.
    </p>
  </body>
</html>"""


def project_invitation_email(name: str, project_name: str, role: str) -> str:
    return f"""\
<html>
  <body style="font-family: sans-serif; color: #222;">
    <p>Hi {name},</p>
    <p>
      You have been added to the project <strong>{project_name}</strong>
      as a <strong>{role}</strong>.
    </p>
  </body>
</html>"""


def status_change_notification_email(
    name: str, task_title: str, project_name: str, old_status: str, new_status: str
) -> str:
    return f"""\
<html>
  <body style="font-family: sans-serif; color: #222;">
    <p>Hi {name},</p>
    <p>
      The status of task <strong>{task_title}</strong>
      in project <strong>{project_name}</strong>
      changed from <strong>{old_status}</strong> to <strong>{new_status}</strong>.
    </p>
  </body>
</html>"""


def assignment_notification_email(name: str, task_title: str, project_name: str) -> str:
    return f"""\
<html>
  <body style="font-family: sans-serif; color: #222;">
    <p>Hi {name},</p>
    <p>
      You have been assigned to the task <strong>{task_title}</strong>
      in project <strong>{project_name}</strong>.
    </p>
  </body>
</html>"""
