// Drives DGII's pre-validator form in process, from #217's research run:
// selects the 606 format, types the reporter and the period, sets the stream
// the file dialog would set, presses Verificar, waits for the form's worker
// and prints what the form shows, then exits. Usage:
//   mono Harness.exe <app dir> <606 file> <rnc> <periodo>
//
// It reads the form's private members by name, so it holds only for the
// binary whose hash `prevalidate 606` pins. The printed block is what
// `docmatch.export.prevalidate.read_verdict` reads:
//   status: <the status label>
//   errors: <the error count label, blank when there is none>
//   error box:
//   <the error box, one numbered line per error>
//
// One change to what the form does, found on #229: it reports progress by
// late-binding a percentage string such as "66.6666666666667" onto its
// progress bar's integer Value. .NET Framework's Visual Basic runtime
// converts that string; Mono's throws and the worker thread dies. Measured
// on #229: every made-up file of two lines crashed, every one of one line
// ran. Read from the form's IL, not run: the percentage is whole, and so
// safe, only when the line count plus one divides 100 (unverified). The
// form's SetText routes by the control's type name (Label gets Text, TextBox
// gets the numbered error line, anything else gets Value), so the harness
// puts a progress bar named Label in its place: the percentage lands on its
// Text and is never shown. The checks and the error box are untouched.
using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

static class Harness
{
    // Named Label on purpose: see the header. Renaming it brings the crash back.
    sealed class Label : ProgressBar { }

    const BindingFlags Any = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    const string Completed = "Proceso Completado";
    const int TickMs = 500;
    const int MaxTicks = 240;

    static object Get(object o, string name)
    {
        var p = o.GetType().GetProperty(name, Any);
        if (p != null) return p.GetValue(o, null);
        return o.GetType().GetField(name, Any).GetValue(o);
    }

    static void Set(object o, string field, object value)
    {
        o.GetType().GetField(field, Any).SetValue(o, value);
    }

    static string Text(object form, string control)
    {
        return ((Control)Get(form, control)).Text;
    }

    [STAThread]
    static int Main(string[] a)
    {
        // UTF-8 whatever the locale, without a byte order mark: the form's
        // messages carry accents and `read_verdict` decodes UTF-8.
        Console.OutputEncoding = new UTF8Encoding(false);
        var asm = Assembly.LoadFrom(Path.Combine(a[0], "DGII.PreValidado.exe"));
        var form = (Form)Activator.CreateInstance(asm.GetType("DGII.PreValidado.frm_Verified"));
        var timer = new Timer { Interval = TickMs };
        bool started = false;
        int ticks = 0;
        timer.Tick += (s, e) =>
        {
            ticks++;
            if (!started)
            {
                started = true;
                var combo = (ComboBox)Get(form, "cbFormato");
                for (int i = 0; i < combo.Items.Count; i++)
                    if (combo.Items[i].ToString().StartsWith("606")) combo.SelectedIndex = i;
                ((TextBox)Get(form, "txtRnc")).Text = a[2];
                ((TextBox)Get(form, "txtPeriodo")).Text = a[3];
                Set(form, "_pgBar", new Label());
                Set(form, "strm", File.OpenRead(a[1]));
                ((TextBox)Get(form, "txtpath")).Text = a[1];
                ((Button)Get(form, "btnVerified")).PerformClick();
                return;
            }
            var status = Text(form, "lblDetalleBarra");
            if (status != Completed && ticks < MaxTicks) return;
            timer.Stop();
            Console.WriteLine("status: " + status);
            Console.WriteLine("errors: " + Text(form, "lblErrores"));
            Console.WriteLine("error box:");
            Console.WriteLine(Text(form, "txtError"));
            Console.Out.Flush();
            Environment.Exit(status == Completed ? 0 : 2);
        };
        form.Shown += (s, e) => timer.Start();
        Application.Run(form);
        return 3;
    }
}
