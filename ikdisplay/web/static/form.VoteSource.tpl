<form dojoType="dijit.form.Form">
    <div>
        Question:
        <div dojoType="dojo.data.ItemFileReadStore" jsId="things" url="/api/selectThings"></div>
        <select name="question" required="false" dojoType="dijit.form.FilteringSelect" store="things" {.section question}value="{_id}"{.end} searchAttr="title" ></select>
    </div>

    <div>
        Via:
        <input name="via" dojoType="dijit.form.TextBox" {.section via}value="{@}"{.end} />
    </div>

    <div>
        Template:
        <textarea name="template" dojoType="dijit.form.Textarea">{.section template}{@}{.end}</textarea>
    </div>

    <button dojoType="dijit.form.Button" onClick="BackChannel.actions.updateItem({_id}, this);">Save</button>
</form>
